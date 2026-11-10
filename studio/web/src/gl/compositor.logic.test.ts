import { describe, expect, it } from "vitest";
import type { Asset, Clip, Project } from "../api/types";
import { activeClips, clipUniforms, fadeFactor, sourceTimeMs } from "./compositor";
import { applyH } from "./homography";

const clip = (o: Partial<Clip>): Clip => ({
  id: "c",
  asset_id: "a",
  generator: null,
  surface_id: "canvas",
  start_ms: 0,
  duration_ms: 1000,
  in_offset_ms: 0,
  loop: false,
  fit: "stretch",
  opacity: 1,
  fade_in_ms: 0,
  fade_out_ms: 0,
  blend: "normal",
  ...o,
});
const asset = (o: Partial<Asset>): Asset => ({
  id: "a",
  kind: "video",
  filename: "a.mp4",
  original_name: "a.mp4",
  width: 100,
  height: 100,
  duration_ms: 2000,
  fps: 30,
  has_audio: false,
  has_alpha: false,
  ...o,
});
const project = (clips: Clip[][]): Project =>
  ({
    schema_version: 1,
    id: "p",
    name: "p",
    revision: 1,
    projector: { width: 1920, height: 1080, fps: 30 },
    calibration_id: null,
    surfaces: [],
    assets: [asset({})],
    timeline: {
      duration_ms: 10000,
      audio_asset_id: null,
      tracks: clips.map((cs, i) => ({ id: `t${i}`, name: `t${i}`, clips: cs })),
    },
    markers: [],
    fpp: { host: null, playlist_name: null },
  }) as Project;

describe("activeClips", () => {
  it("returns track order, excludes out-of-range, includes generators", () => {
    const p = project([
      [clip({ id: "a" }), clip({ id: "late", start_ms: 500 })],
      [clip({ id: "gen", asset_id: null, generator: { type: "solid", params: {} } })],
      [clip({ id: "b" })],
    ]);
    expect(activeClips(p, 100).map((c) => c.id)).toEqual(["a", "gen", "b"]);
    expect(activeClips(p, 600).map((c) => c.id)).toEqual(["a", "late", "gen", "b"]);
    expect(activeClips(p, 1600)).toEqual([]);
  });
});

describe("sourceTimeMs", () => {
  it("loops, holds, and ignores for images", () => {
    expect(sourceTimeMs(clip({ loop: true }), asset({}), 2500)).toBe(500);
    expect(sourceTimeMs(clip({ loop: false }), asset({}), 2500)).toBe(2500);
    expect(sourceTimeMs(clip({}), asset({ kind: "image", duration_ms: null }), 2500)).toBe(0);
  });
});

describe("fadeFactor", () => {
  it("ramps", () => {
    expect(fadeFactor(clip({ fade_in_ms: 1000, duration_ms: 5000 }), 500)).toBeCloseTo(0.5);
    expect(fadeFactor(clip({}), 500)).toBe(1);
    expect(
      fadeFactor(clip({ duration_ms: 400, fade_in_ms: 400, fade_out_ms: 400 }), 200),
    ).toBeCloseTo(0.25);
  });
});

describe("clipUniforms", () => {
  it("stretch on canvas", () => {
    const u = clipUniforms(project([[clip({})]]), clip({}), asset({}), 640, 360)!;
    expect(u.dst).toEqual([0, 0, 1, 1]);
    expect(u.src).toEqual([0, 0, 1, 1]);
    const p = applyH(u.invH, [1920, 1080]);
    expect(p[0]).toBeCloseTo(1, 6);
    expect(p[1]).toBeCloseTo(1, 6);
  });
  it("missing surface gives null", () => {
    expect(clipUniforms(project([]), clip({ surface_id: "nope" }), asset({}), 10, 10)).toBeNull();
  });
});
