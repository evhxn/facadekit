import { describe, expect, it } from "vitest";
import type { Clip, Marker, Project } from "../../api/types";
import { alternateSurfaces, beatsInRange, splitClipOnBeats } from "./beatOps";

function clip(over: Partial<Clip> = {}): Clip {
  return {
    id: "clp_a",
    asset_id: "ast_v",
    generator: null,
    surface_id: "canvas",
    start_ms: 0,
    duration_ms: 4000,
    in_offset_ms: 0,
    loop: false,
    fit: "fill",
    opacity: 1,
    fade_in_ms: 0,
    fade_out_ms: 0,
    blend: "normal",
    ...over,
  };
}

function proj(clips: Clip[], markers: Marker[]): Project {
  return {
    schema_version: 1,
    id: "prj_1",
    name: "T",
    revision: 1,
    projector: { width: 1920, height: 1080, fps: 30 },
    calibration_id: null,
    surfaces: [],
    assets: [],
    timeline: {
      duration_ms: 10000,
      audio_asset_id: null,
      tracks: [{ id: "trk_1", name: "T", clips }],
    },
    markers,
    fpp: { host: null, playlist_name: null },
  };
}

const beats = (step: number, to: number): Marker[] =>
  Array.from({ length: Math.floor(to / step) + 1 }, (_, i) => ({ t_ms: i * step, kind: "beat" }));

describe("beatsInRange", () => {
  it("returns only beats in [start, end)", () => {
    const m: Marker[] = [
      ...beats(500, 2000),
      { t_ms: 750, kind: "onset" },
      { t_ms: 900, kind: "user" },
    ];
    expect(beatsInRange(m, 500, 1500)).toEqual([500, 1000]);
  });
});

describe("splitClipOnBeats", () => {
  it("splits every 2 beats into tiling pieces", () => {
    const p = proj([clip({ loop: true })], beats(500, 8000));
    const ids = splitClipOnBeats(p, "clp_a", 2);
    const clips = p.timeline.tracks[0]!.clips;
    expect(ids).toHaveLength(4);
    expect(clips.map((c) => c.id)).toEqual(ids);
    expect(clips.map((c) => c.duration_ms)).toEqual([1000, 1000, 1000, 1000]);
    expect(clips.map((c) => c.start_ms)).toEqual([0, 1000, 2000, 3000]);
    expect(clips.map((c) => c.in_offset_ms)).toEqual([0, 1000, 2000, 3000]);
    expect(new Set(ids).size).toBe(4);
    expect(clips.every((c) => c.loop && c.asset_id === "ast_v")).toBe(true);
  });

  it("keeps fades only on the outer edges", () => {
    const p = proj([clip({ fade_in_ms: 200, fade_out_ms: 300 })], beats(1000, 8000));
    splitClipOnBeats(p, "clp_a", 1);
    const c = p.timeline.tracks[0]!.clips;
    expect(c.map((x) => x.fade_in_ms)).toEqual([200, 0, 0, 0]);
    expect(c.map((x) => x.fade_out_ms)).toEqual([0, 0, 0, 300]);
  });

  it("leaves a clip with no beats inside unchanged", () => {
    const p = proj([clip({ start_ms: 100, duration_ms: 300 })], beats(500, 8000));
    expect(splitClipOnBeats(p, "clp_a", 1)).toEqual(["clp_a"]);
    expect(p.timeline.tracks[0]!.clips).toHaveLength(1);
    expect(p.timeline.tracks[0]!.clips[0]!.duration_ms).toBe(300);
  });
});

describe("alternateSurfaces", () => {
  it("cycles through the given surfaces", () => {
    const clips = ["a", "b", "c", "d", "e"].map((s) => clip({ id: s }));
    const p = proj(clips, []);
    alternateSurfaces(p, ["a", "b", "c", "d", "e"], ["s1", "s2"]);
    expect(p.timeline.tracks[0]!.clips.map((c) => c.surface_id)).toEqual([
      "s1",
      "s2",
      "s1",
      "s2",
      "s1",
    ]);
  });
});
