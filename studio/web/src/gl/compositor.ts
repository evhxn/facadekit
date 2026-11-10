import { getProject, assetUrl as apiAssetUrl } from "../api/client";
import type { Asset, Clip, Project, Surface } from "../api/types";
import type { LiveRenderer } from "../projector/ProjectorPage";
import type { TransportMessage } from "../projector/projectorSocket";
import {
  estimateAspect,
  fitRects,
  invert,
  tryQuadHomography,
  type Mat3,
  type Pt,
  type Rect,
} from "./homography";
import { generatorUniforms } from "./generators";
import { MaskTextureCache } from "./maskTexture";
import { buildMeshVertices, type MeshVertices } from "./meshGeometry";
import { isZeroMesh } from "../pages/surfaces/meshMath";
import { FRAGMENT_SRC, VERTEX_SRC } from "./shaders";
import { ImageSource, VideoSource, type ClipTextureSource, type FrameSource } from "./sources";

export type { FrameSource };

/** Clips active at tMs, in track order (media and generator clips). */
export function activeClips(project: Project, tMs: number): Clip[] {
  const out: Clip[] = [];
  for (const track of project.timeline.tracks) {
    for (const clip of track.clips) {
      if (clip.start_ms <= tMs && tMs < clip.start_ms + clip.duration_ms) out.push(clip);
    }
  }
  return out;
}

/** Time within the source asset, in ms. Non-looping clips are not clamped here. */
export function sourceTimeMs(clip: Clip, asset: Asset | null | undefined, tMs: number): number {
  if (asset && asset.kind === "image") return 0;
  let s = clip.in_offset_ms + (tMs - clip.start_ms);
  if (clip.loop && asset?.duration_ms && asset.duration_ms > 0) {
    s = ((s % asset.duration_ms) + asset.duration_ms) % asset.duration_ms;
  }
  return s;
}

export function fadeFactor(clip: Clip, tMs: number): number {
  const fin = clip.fade_in_ms > 0 ? Math.min(1, (tMs - clip.start_ms) / clip.fade_in_ms) : 1;
  const end = clip.start_ms + clip.duration_ms;
  const fout = clip.fade_out_ms > 0 ? Math.min(1, (end - tMs) / clip.fade_out_ms) : 1;
  return Math.max(0, fin) * Math.max(0, fout);
}

export interface ClipUniforms {
  /** Inverse of quadHomography(1, 1, quad), row-major. */
  invH: Mat3;
  /** Normalised source rect (0..1). */
  src: Rect;
  /** Destination rect inside the surface unit square. */
  dst: Rect;
  featherUv: [number, number];
  blend: Clip["blend"];
}

const dist = (p: Pt, q: Pt) => Math.hypot(p[0] - q[0], p[1] - q[1]);

/** Null when the surface is missing or its quad is degenerate (caller skips drawing). */
export function clipUniforms(
  project: Project,
  clip: Clip,
  _asset: Asset | null | undefined,
  srcW: number,
  srcH: number,
): ClipUniforms | null {
  if (!(srcW > 0) || !(srcH > 0)) return null;
  const { width: W, height: H } = project.projector;
  let quad: Pt[];
  let aspect: number;
  let featherPx = 0;
  if (clip.surface_id === "canvas") {
    quad = [
      [0, 0],
      [W, 0],
      [W, H],
      [0, H],
    ];
    aspect = W / H;
  } else {
    const s = project.surfaces.find((x) => x.id === clip.surface_id);
    if (!s) return null;
    quad = s.quad;
    aspect = s.aspect > 0 ? s.aspect : estimateAspect(quad);
    featherPx = s.feather_px;
  }
  const h = tryQuadHomography(1, 1, quad);
  if (!h) return null;
  // Generator clips are evaluated in the unit square and drawn like stretched media.
  const { src, dst } = fitRects(srcW, srcH, aspect, clip.generator ? "stretch" : clip.fit);
  const avgW = (dist(quad[0]!, quad[1]!) + dist(quad[3]!, quad[2]!)) / 2;
  const avgH = (dist(quad[0]!, quad[3]!) + dist(quad[1]!, quad[2]!)) / 2;
  const featherUv: [number, number] =
    featherPx > 0 && avgW > 0 && avgH > 0 ? [featherPx / avgW, featherPx / avgH] : [0, 0];
  return {
    invH: invert(h),
    src: [src[0] / srcW, src[1] / srcH, src[2] / srcW, src[3] / srcH],
    dst,
    featherUv,
    blend: clip.blend,
  };
}

const transpose = (m: Mat3): Float32Array =>
  new Float32Array([m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]]);

interface Entry {
  assetId: string;
  source: ClipTextureSource;
}

const LOOKAHEAD_MS = 2000;

export class PreviewCompositor {
  private gl: WebGL2RenderingContext | null;
  private program: WebGLProgram | null = null;
  private buffer: WebGLBuffer | null = null;
  private uvwBuffer: WebGLBuffer | null = null;
  private indexBuffer: WebGLBuffer | null = null;
  private meshCache = new Map<string, { surface: Surface; verts: MeshVertices }>();
  private loc: Record<string, WebGLUniformLocation | null> = {};
  private project: Project | null = null;
  private sources = new Map<string, Entry>();
  private masks = new MaskTextureCache();
  private dummyMask: WebGLTexture | null = null;

  constructor(
    private canvas: HTMLCanvasElement,
    private opts: { assetUrl: (assetId: string) => string; preserveDrawingBuffer?: boolean },
  ) {
    this.gl = canvas.getContext("webgl2", {
      alpha: false,
      premultipliedAlpha: true,
      colorSpace: "srgb",
      preserveDrawingBuffer: opts.preserveDrawingBuffer ?? false,
    } as WebGLContextAttributes);
    if (this.gl) this.init(this.gl);
  }

  private init(gl: WebGL2RenderingContext): void {
    const compile = (type: number, src: string) => {
      const sh = gl.createShader(type)!;
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        throw new Error(`shader compile failed: ${gl.getShaderInfoLog(sh)}`);
      }
      return sh;
    };
    const p = gl.createProgram()!;
    gl.attachShader(p, compile(gl.VERTEX_SHADER, VERTEX_SRC));
    gl.attachShader(p, compile(gl.FRAGMENT_SHADER, FRAGMENT_SRC));
    gl.bindAttribLocation(p, 0, "a_pos");
    gl.bindAttribLocation(p, 1, "a_uvw");
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      throw new Error(`program link failed: ${gl.getProgramInfoLog(p)}`);
    }
    this.program = p;
    for (const n of [
      "u_size",
      "u_tex",
      "u_invH",
      "u_src",
      "u_dst",
      "u_opacity",
      "u_featherUv",
      "u_mask",
      "u_maskOn",
      "u_genType",
      "u_genColA",
      "u_genColB",
      "u_genP",
      "u_genTc",
      "u_meshOn",
    ]) {
      this.loc[n] = gl.getUniformLocation(p, n);
    }
    this.buffer = gl.createBuffer();
    this.uvwBuffer = gl.createBuffer();
    this.indexBuffer = gl.createBuffer();
    gl.enable(gl.BLEND);
    this.dummyMask = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.dummyMask);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.R8,
      1,
      1,
      0,
      gl.RED,
      gl.UNSIGNED_BYTE,
      new Uint8Array([255]),
    );
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  }

  setProject(project: Project): void {
    this.project = project;
    const gl = this.gl;
    const seen = new Set<string>();
    for (const track of project.timeline.tracks) {
      for (const clip of track.clips) {
        if (clip.generator || !clip.asset_id) continue;
        seen.add(clip.id);
        const existing = this.sources.get(clip.id);
        if (existing && existing.assetId === clip.asset_id) continue;
        if (existing && gl) existing.source.dispose(gl);
        const asset = project.assets.find((a) => a.id === clip.asset_id);
        if (!asset || asset.kind === "audio") {
          this.sources.delete(clip.id);
          continue;
        }
        const url = this.opts.assetUrl(clip.asset_id);
        const source =
          asset.kind === "image" ? new ImageSource(url) : new VideoSource(url, asset.duration_ms);
        this.sources.set(clip.id, { assetId: clip.asset_id, source });
      }
    }
    for (const [id, entry] of this.sources) {
      if (!seen.has(id)) {
        if (gl) entry.source.dispose(gl);
        this.sources.delete(id);
      }
    }
  }

  /** Resolves once every image source has loaded (video sources are not awaited). */
  ready(timeoutMs = 15000): Promise<void> {
    const started = performance.now();
    return new Promise((resolve, reject) => {
      const check = () => {
        const pending = [...this.sources.values()].some(
          (e) => e.source instanceof ImageSource && !(e.source.width > 0),
        );
        if (!pending) resolve();
        else if (performance.now() - started > timeoutMs) reject(new Error("sources not ready"));
        else setTimeout(check, 20);
      };
      check();
    });
  }

  render(tMs: number, playing: boolean): void {
    const gl = this.gl;
    const project = this.project;
    if (!gl || !this.program || !project) return;
    const { width, height } = this.canvas;
    gl.viewport(0, 0, width, height);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);

    const active = activeClips(project, tMs);
    const activeIds = new Set(active.map((c) => c.id));
    for (const track of project.timeline.tracks) {
      for (const clip of track.clips) {
        const e = this.sources.get(clip.id);
        if (!e || activeIds.has(clip.id)) continue;
        if (!(clip.start_ms <= tMs + LOOKAHEAD_MS && tMs < clip.start_ms + clip.duration_ms)) {
          e.source.idle();
        }
      }
    }

    gl.useProgram(this.program);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.vertexAttrib3f(1, 0, 0, 1);
    gl.uniform2f(this.loc.u_size!, width, height);
    gl.uniform1i(this.loc.u_tex!, 0);
    gl.uniform1i(this.loc.u_mask!, 1);
    const maskedIds = new Set<string>();
    for (const s of project.surfaces) if (s.mask) maskedIds.add(s.id);
    this.masks.prune(gl, maskedIds);
    gl.activeTexture(gl.TEXTURE0);

    for (const clip of active) {
      const gen = clip.generator;
      const entry = gen ? undefined : this.sources.get(clip.id);
      if (!gen && !entry) continue;
      const asset = project.assets.find((a) => a.id === clip.asset_id);
      let tex: WebGLTexture | null;
      let u: ClipUniforms | null;
      if (gen) {
        tex = this.dummyMask;
        u = clipUniforms(project, clip, asset, 1, 1);
      } else {
        entry!.source.setPlaying(playing);
        tex = entry!.source.texture(gl, sourceTimeMs(clip, asset, tMs));
        if (!tex) continue;
        u = clipUniforms(project, clip, asset, entry!.source.width, entry!.source.height);
      }
      if (!u) continue;
      const gu = gen ? generatorUniforms(gen, (tMs - clip.start_ms) / 1000) : null;
      gl.uniform1i(this.loc.u_genType!, gu ? gu.type : 0);
      if (gu) {
        gl.uniform3f(this.loc.u_genColA!, ...gu.colA);
        gl.uniform3f(this.loc.u_genColB!, ...gu.colB);
        gl.uniform4f(this.loc.u_genP!, ...gu.p);
        gl.uniform1f(this.loc.u_genTc!, gu.tc);
      }
      const quad = this.surfaceQuad(project, clip);
      if (!quad) continue;
      const surface = project.surfaces.find((x) => x.id === clip.surface_id);
      const maskTex = surface?.mask
        ? this.masks.get(
            gl,
            surface.id,
            surface.mask,
            surface.feather_px,
            project.projector.width,
            project.projector.height,
          )
        : null;
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, maskTex ?? this.dummyMask);
      gl.activeTexture(gl.TEXTURE0);
      gl.uniform1f(this.loc.u_maskOn!, maskTex ? 1 : 0);
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.uniformMatrix3fv(this.loc.u_invH!, false, transpose(u.invH));
      gl.uniform4f(this.loc.u_src!, ...u.src);
      gl.uniform4f(this.loc.u_dst!, ...u.dst);
      gl.uniform1f(this.loc.u_opacity!, clip.opacity * fadeFactor(clip, tMs));
      // A mask carries its own (blurred) edge; the analytic edge feather applies to plain quads.
      const [fu, fv] = maskTex ? [0, 0] : u.featherUv;
      gl.uniform2f(this.loc.u_featherUv!, fu, fv);
      if (u.blend === "add") gl.blendFunc(gl.ONE, gl.ONE);
      else if (u.blend === "screen") gl.blendFunc(gl.ONE_MINUS_DST_COLOR, gl.ONE);
      else gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      const meshed =
        surface && surface.mesh && !isZeroMesh(surface.mesh) ? this.meshVerts(surface) : null;
      if (meshed) {
        gl.uniform1f(this.loc.u_meshOn!, 1);
        gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
        gl.bufferData(gl.ARRAY_BUFFER, meshed.positions, gl.DYNAMIC_DRAW);
        gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
        gl.bindBuffer(gl.ARRAY_BUFFER, this.uvwBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, meshed.uvw, gl.DYNAMIC_DRAW);
        gl.enableVertexAttribArray(1);
        gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 0, 0);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.indexBuffer);
        gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, meshed.indices, gl.DYNAMIC_DRAW);
        gl.drawElements(gl.TRIANGLES, meshed.indices.length, gl.UNSIGNED_SHORT, 0);
        gl.disableVertexAttribArray(1);
        gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
        continue;
      }
      gl.uniform1f(this.loc.u_meshOn!, 0);
      const [a, b, c, d] = quad as [Pt, Pt, Pt, Pt];
      gl.bufferData(
        gl.ARRAY_BUFFER,
        new Float32Array([...a, ...b, ...c, ...a, ...c, ...d]),
        gl.DYNAMIC_DRAW,
      );
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    }
  }

  private meshVerts(surface: Surface): MeshVertices {
    const hit = this.meshCache.get(surface.id);
    if (hit && hit.surface === surface) return hit.verts;
    const verts = buildMeshVertices(surface, 4);
    this.meshCache.set(surface.id, { surface, verts });
    return verts;
  }

  private surfaceQuad(project: Project, clip: Clip): Pt[] | null {
    if (clip.surface_id === "canvas") {
      const { width: W, height: H } = project.projector;
      return [
        [0, 0],
        [W, 0],
        [W, H],
        [0, H],
      ];
    }
    return project.surfaces.find((s) => s.id === clip.surface_id)?.quad ?? null;
  }

  dispose(): void {
    const gl = this.gl;
    if (gl) {
      for (const e of this.sources.values()) e.source.dispose(gl);
      this.masks.dispose(gl);
      if (this.dummyMask) gl.deleteTexture(this.dummyMask);
      if (this.program) gl.deleteProgram(this.program);
      if (this.buffer) gl.deleteBuffer(this.buffer);
      if (this.uvwBuffer) gl.deleteBuffer(this.uvwBuffer);
      if (this.indexBuffer) gl.deleteBuffer(this.indexBuffer);
    }
    this.sources.clear();
    this.program = null;
    this.buffer = null;
    this.uvwBuffer = null;
    this.indexBuffer = null;
    this.meshCache.clear();
    this.dummyMask = null;
    this.project = null;
  }
}

/** Live renderer for the projector window: own canvas above the 2D one, rAF loop. */
export function createLiveRenderer(): LiveRenderer {
  let canvas: HTMLCanvasElement | null = null;
  let compositor: PreviewCompositor | null = null;
  let raf = 0;
  let generation = 0;
  let loadedRevision = -1;
  let requestedRevision = -1;
  let anchor = { tMs: 0, at: 0, playing: false };

  const load = (projectId: string, gen: number) => {
    void getProject(projectId)
      .then((p) => {
        if (gen !== generation || !canvas || !compositor) return;
        loadedRevision = p.revision;
        canvas.width = p.projector.width;
        canvas.height = p.projector.height;
        compositor.setProject(p);
      })
      .catch(() => undefined);
  };

  const teardown = () => {
    generation++;
    cancelAnimationFrame(raf);
    compositor?.dispose();
    canvas?.remove();
    compositor = null;
    canvas = null;
    loadedRevision = -1;
    requestedRevision = -1;
  };

  let currentProject = "";

  return {
    start(projectId, base) {
      teardown();
      currentProject = projectId;
      const c = document.createElement("canvas");
      c.style.cssText = "position:fixed;inset:0;width:100vw;height:100vh;background:#000";
      c.width = base.width;
      c.height = base.height;
      (base.parentElement ?? document.body).appendChild(c);
      canvas = c;
      compositor = new PreviewCompositor(c, {
        assetUrl: (aid) => apiAssetUrl(projectId, aid),
      });
      anchor = { tMs: 0, at: performance.now(), playing: false };
      load(projectId, generation);
      const loop = () => {
        const t = anchor.playing ? anchor.tMs + (performance.now() - anchor.at) : anchor.tMs;
        compositor?.render(t, anchor.playing);
        raf = requestAnimationFrame(loop);
      };
      raf = requestAnimationFrame(loop);
    },
    transport(msg: TransportMessage) {
      anchor = { tMs: msg.t_ms, at: performance.now(), playing: msg.playing };
      if (msg.revision !== loadedRevision && msg.revision !== requestedRevision && compositor) {
        requestedRevision = msg.revision;
        load(currentProject, generation);
      }
    },
    stop() {
      teardown();
    },
  };
}
