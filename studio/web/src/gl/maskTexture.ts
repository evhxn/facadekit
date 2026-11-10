import type { Mask } from "../api/types";

/** The mask texture is rasterised at projector size / MASK_SCALE_DIV. */
export const MASK_SCALE_DIV = 2;

export function maskTextureSize(projW: number, projH: number): [number, number] {
  return [
    Math.max(1, Math.ceil(projW / MASK_SCALE_DIV)),
    Math.max(1, Math.ceil(projH / MASK_SCALE_DIV)),
  ];
}

/** Blur radius (CSS px) on the half-size canvas: backend sigma is feather_px / 2 at full size. */
export function maskBlurPx(featherPx: number): number {
  return featherPx > 0 ? featherPx / (2 * MASK_SCALE_DIV) : 0;
}

/** Changes exactly when the rasterised texture would change. */
export function maskCacheKey(mask: Mask, featherPx: number, projW: number, projH: number): string {
  return JSON.stringify([projW, projH, featherPx, mask.outer, mask.holes]);
}

function draw(ctx: CanvasRenderingContext2D, mask: Mask, w: number, h: number): void {
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, w, h);
  ctx.beginPath();
  for (const ring of [mask.outer, ...mask.holes]) {
    ring.forEach((p, i) => {
      const x = p[0] / MASK_SCALE_DIV;
      const y = p[1] / MASK_SCALE_DIV;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.closePath();
  }
  ctx.fillStyle = "#fff";
  ctx.fill("evenodd");
}

/** Rasterise outer (white) minus holes (black, evenodd), blurred when feather_px > 0. */
export function rasterizeMaskCanvas(
  mask: Mask,
  featherPx: number,
  projW: number,
  projH: number,
): HTMLCanvasElement | null {
  const [w, h] = maskTextureSize(projW, projH);
  const sharp = document.createElement("canvas");
  sharp.width = w;
  sharp.height = h;
  const sctx = sharp.getContext("2d");
  if (!sctx) return null;
  draw(sctx, mask, w, h);
  const blur = maskBlurPx(featherPx);
  if (blur <= 0) return sharp;
  const out = document.createElement("canvas");
  out.width = w;
  out.height = h;
  const octx = out.getContext("2d");
  if (!octx) return sharp;
  octx.fillStyle = "#000";
  octx.fillRect(0, 0, w, h);
  octx.filter = `blur(${blur}px)`;
  octx.drawImage(sharp, 0, 0);
  octx.filter = "none";
  return out;
}

interface Entry {
  mask: Mask;
  feather: number;
  key: string;
  texture: WebGLTexture;
}

/** One R8 texture per surface, rebuilt only when the mask, feather or projector size changes. */
export class MaskTextureCache {
  private entries = new Map<string, Entry>();
  private rebuilds = 0;

  get rebuildCount(): number {
    return this.rebuilds;
  }

  get(
    gl: WebGL2RenderingContext,
    surfaceId: string,
    mask: Mask,
    featherPx: number,
    projW: number,
    projH: number,
  ): WebGLTexture | null {
    const e = this.entries.get(surfaceId);
    if (e && e.mask === mask && e.feather === featherPx) return e.texture;
    const key = maskCacheKey(mask, featherPx, projW, projH);
    if (e && e.key === key) {
      e.mask = mask;
      e.feather = featherPx;
      return e.texture;
    }
    const canvas = rasterizeMaskCanvas(mask, featherPx, projW, projH);
    if (!canvas) return e?.texture ?? null;
    const texture = e?.texture ?? gl.createTexture();
    if (!texture) return null;
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, gl.RED, gl.UNSIGNED_BYTE, canvas as never);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    this.rebuilds++;
    this.entries.set(surfaceId, { mask, feather: featherPx, key, texture });
    return texture;
  }

  /** Drop textures of surfaces that no longer have a mask. */
  prune(gl: WebGL2RenderingContext, keep: Set<string>): void {
    for (const [id, e] of this.entries) {
      if (!keep.has(id)) {
        gl.deleteTexture(e.texture);
        this.entries.delete(id);
      }
    }
  }

  dispose(gl: WebGL2RenderingContext): void {
    for (const e of this.entries.values()) gl.deleteTexture(e.texture);
    this.entries.clear();
  }
}
