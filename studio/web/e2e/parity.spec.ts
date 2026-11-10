import { join } from "node:path";
import { mkdirSync, writeFileSync } from "node:fs";
import { expect, test, type APIRequestContext } from "@playwright/test";
import jpeg from "jpeg-js";
import { PNG } from "pngjs";
import { PROJECTOR, scenes, type Pt, type Scene } from "./scenes";

interface Img {
  width: number;
  height: number;
  data: Uint8Array; // RGBA
}

const API = "/api/v1";

/** Mask of pixels within 3 px of any quad edge (7 px wide stroke). */
export function edgeMask(width: number, height: number, quads: Pt[][]): Uint8Array {
  const mask = new Uint8Array(width * height);
  const half = 3.5;
  for (const quad of quads) {
    for (let i = 0; i < quad.length; i++) {
      const [ax, ay] = quad[i]!;
      const [bx, by] = quad[(i + 1) % quad.length]!;
      const minX = Math.max(0, Math.floor(Math.min(ax, bx) - half - 1));
      const maxX = Math.min(width - 1, Math.ceil(Math.max(ax, bx) + half + 1));
      const minY = Math.max(0, Math.floor(Math.min(ay, by) - half - 1));
      const maxY = Math.min(height - 1, Math.ceil(Math.max(ay, by) + half + 1));
      const dx = bx - ax;
      const dy = by - ay;
      const len2 = dx * dx + dy * dy || 1;
      for (let y = minY; y <= maxY; y++) {
        for (let x = minX; x <= maxX; x++) {
          const t = Math.max(0, Math.min(1, ((x + 0.5 - ax) * dx + (y + 0.5 - ay) * dy) / len2));
          const d = Math.hypot(x + 0.5 - (ax + t * dx), y + 0.5 - (ay + t * dy));
          if (d <= half) mask[y * width + x] = 1;
        }
      }
    }
  }
  return mask;
}

export interface Parity {
  mad: number;
  bigFraction: number;
  compared: number;
  diff: PNG;
}

export function compare(a: Img, b: Img, mask: Uint8Array): Parity {
  const diff = new PNG({ width: a.width, height: a.height });
  let sum = 0;
  let big = 0;
  let compared = 0;
  for (let p = 0; p < a.width * a.height; p++) {
    const i = p * 4;
    const dr = Math.abs(a.data[i]! - b.data[i]!);
    const dg = Math.abs(a.data[i + 1]! - b.data[i + 1]!);
    const db = Math.abs(a.data[i + 2]! - b.data[i + 2]!);
    const m = Math.max(dr, dg, db);
    const masked = mask[p] === 1;
    if (!masked) {
      compared++;
      sum += dr + dg + db;
      if (m > 24) big++;
    }
    diff.data[i] = Math.min(255, m * 4);
    diff.data[i + 1] = masked ? 80 : 0;
    diff.data[i + 2] = masked ? 80 : 0;
    diff.data[i + 3] = 255;
  }
  return {
    mad: sum / (compared * 3),
    bigFraction: big / compared,
    compared,
    diff,
  };
}

async function buildProject(request: APIRequestContext, scene: Scene): Promise<string> {
  const created = await request.post(`${API}/projects`, {
    data: { name: `parity ${scene.name} ${Date.now()}`, projector: PROJECTOR },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  let project = await created.json();
  const pid: string = project.id;

  const assetIds: Record<string, string> = {};
  for (const [key, buffer] of Object.entries(scene.assets)) {
    const res = await request.post(`${API}/projects/${pid}/assets`, {
      multipart: { file: { name: `${key}.png`, mimeType: "image/png", buffer } },
    });
    expect(res.ok(), await res.text()).toBeTruthy();
    const body = await res.json();
    assetIds[key] = body.asset.id;
    project = body.project;
  }

  const surfaceIds: Record<string, string> = {};
  const surfaces = Object.entries(scene.surfaces).map(([key, s], i) => {
    const id = `srf_0000000${i + 1}`;
    surfaceIds[key] = id;
    return {
      id,
      name: key,
      quad: s.quad,
      mask: s.mask ?? null,
      feather_px: s.feather_px ?? 0,
      aspect: s.aspect,
      mesh: null,
    };
  });

  const tracks: { id: string; name: string; clips: unknown[] }[] = [];
  scene.clips.forEach((c, i) => {
    while (tracks.length <= c.track) {
      const n = tracks.length;
      tracks.push({ id: `trk_0000000${n + 1}`, name: `Track ${n + 1}`, clips: [] });
    }
    tracks[c.track]!.clips.push({
      id: `clp_0000000${i + 1}`,
      asset_id: assetIds[c.asset],
      generator: null,
      surface_id: c.surface === "canvas" ? "canvas" : surfaceIds[c.surface],
      start_ms: 0,
      duration_ms: 5000,
      in_offset_ms: 0,
      loop: true,
      fit: c.fit,
      opacity: c.opacity ?? 1,
      fade_in_ms: c.fade_in_ms ?? 0,
      fade_out_ms: 0,
      blend: c.blend ?? "normal",
    });
  });

  const put = await request.put(`${API}/projects/${pid}`, {
    data: {
      ...project,
      surfaces,
      timeline: { ...project.timeline, tracks },
    },
  });
  expect(put.ok(), await put.text()).toBeTruthy();
  return pid;
}

for (const scene of scenes) {
  test(`parity: ${scene.name}`, async ({ page, request }) => {
    const pid = await buildProject(request, scene);

    await page.goto(`/dev/parity?project=${pid}&t=${scene.t_ms}&dev=1`);
    await page.waitForFunction(
      () => (window as unknown as { __parityReady?: boolean }).__parityReady === true,
      undefined,
      { timeout: 30000 },
    );
    const dataUrl = await page.evaluate(() =>
      (document.getElementById("parity-canvas") as HTMLCanvasElement).toDataURL("image/png"),
    );
    const previewBuf = Buffer.from(dataUrl.split(",")[1]!, "base64");
    const preview = PNG.sync.read(previewBuf);

    const frameRes = await request.get(`${API}/projects/${pid}/frame?t_ms=${scene.t_ms}&scale=1`);
    expect(frameRes.ok(), await frameRes.text()).toBeTruthy();
    const backendBuf = await frameRes.body();
    const backend = jpeg.decode(backendBuf, { useTArray: true, formatAsRGBA: true });

    expect([preview.width, preview.height]).toEqual([PROJECTOR.width, PROJECTOR.height]);
    expect([backend.width, backend.height]).toEqual([PROJECTOR.width, PROJECTOR.height]);

    const quads: Pt[][] = Object.values(scene.surfaces).map((s) => s.quad);
    // Mask outlines are edges too (half-size texture vs the backend's exact raster).
    for (const surf of Object.values(scene.surfaces)) {
      if (surf.mask) quads.push(surf.mask.outer, ...surf.mask.holes);
    }
    if (scene.clips.some((c) => c.surface === "canvas")) {
      quads.push([
        [0, 0],
        [PROJECTOR.width, 0],
        [PROJECTOR.width, PROJECTOR.height],
        [0, PROJECTOR.height],
      ]);
    }
    const mask = edgeMask(PROJECTOR.width, PROJECTOR.height, quads);
    const result = compare(
      { width: preview.width, height: preview.height, data: preview.data },
      { width: backend.width, height: backend.height, data: backend.data },
      mask,
    );

    const ok = result.mad < 4 && result.bigFraction < 0.005;
    if (!ok) {
      const dir = join(process.cwd(), "test-results", scene.name);
      mkdirSync(dir, { recursive: true });
      writeFileSync(`${dir}/preview.png`, previewBuf);
      const backendPng = new PNG({ width: backend.width, height: backend.height });
      Buffer.from(backend.data).copy(backendPng.data);
      writeFileSync(`${dir}/backend.png`, PNG.sync.write(backendPng));

      writeFileSync(`${dir}/diff.png`, PNG.sync.write(result.diff));
      console.log(`parity failure artifacts: ${dir}`);
    }
    console.log(
      `${scene.name}: mad=${result.mad.toFixed(3)} big=${(result.bigFraction * 100).toFixed(3)}%`,
    );
    expect(result.mad, "mean abs diff per channel").toBeLessThan(4);
    expect(result.bigFraction, "fraction of pixels differing by > 24").toBeLessThan(0.005);
  });
}
