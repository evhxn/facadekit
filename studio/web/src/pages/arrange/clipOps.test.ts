import { describe, expect, it } from "vitest";
import { makeProject } from "../../api/client.test";
import type { Asset, Clip, Project } from "../../api/types";
import {
  addClip,
  findClip,
  moveClipToTrack,
  overlaps,
  removeClip,
  setShowAudio,
  updateClip,
} from "./clipOps";

function clip(id: string, start_ms: number, duration_ms: number): Clip {
  return {
    id,
    asset_id: "ast_1",
    generator: null,
    surface_id: "canvas",
    start_ms,
    duration_ms,
    in_offset_ms: 0,
    loop: true,
    fit: "fill",
    opacity: 1,
    fade_in_ms: 0,
    fade_out_ms: 0,
    blend: "normal",
  };
}

function project(...tracks: Clip[][]): Project {
  return makeProject({
    timeline: {
      duration_ms: 60000,
      audio_asset_id: null,
      tracks: tracks.map((clips, i) => ({ id: `trk_${i}`, name: `Track ${i + 1}`, clips })),
    },
  });
}

const tracks = (p: Project) => p.timeline.tracks.map((t) => t.clips.map((c) => c.id));

describe("clipOps", () => {
  it("overlaps is false for touching clips", () => {
    expect(overlaps(clip("a", 0, 1000), clip("b", 1000, 1000))).toBe(false);
    expect(overlaps(clip("a", 0, 1001), clip("b", 1000, 1000))).toBe(true);
  });

  it("addClip places an overlapping clip on a new Track 2", () => {
    const p = project([clip("a", 0, 1000)]);
    addClip(p, clip("b", 500, 1000), 0);
    expect(tracks(p)).toEqual([["a"], ["b"]]);
    expect(p.timeline.tracks[1]?.name).toBe("Track 2");
  });

  it("addClip uses the requested track when it fits and 'new' creates a layer", () => {
    const p = project([clip("a", 0, 1000)]);
    addClip(p, clip("b", 1000, 1000), 0);
    addClip(p, clip("c", 0, 10), "new");
    expect(tracks(p)).toEqual([["a", "b"], ["c"]]);
  });

  it("updateClip lengthening into a neighbour moves it up a track", () => {
    const p = project([clip("a", 0, 1000), clip("b", 1000, 1000)]);
    updateClip(p, "a", { duration_ms: 1500 });
    expect(tracks(p)).toEqual([["b"], ["a"]]);
    expect(findClip(p, "a")?.clip.duration_ms).toBe(1500);
  });

  it("updateClip patches in place when there is no overlap", () => {
    const p = project([clip("a", 0, 1000)]);
    updateClip(p, "a", { opacity: 0.5 });
    expect(p.timeline.tracks[0]?.clips[0]?.opacity).toBe(0.5);
  });

  it("removeClip drops an emptied upper track but keeps the last track", () => {
    const p = project([clip("a", 0, 1000)], [clip("b", 0, 1000)]);
    removeClip(p, "b");
    expect(tracks(p)).toEqual([["a"]]);
    removeClip(p, "a");
    expect(tracks(p)).toEqual([[]]);
  });

  it("moveClipToTrack returns false on overlap and leaves the project unchanged", () => {
    const p = project([clip("a", 0, 1000)], [clip("b", 500, 1000)]);
    const before = structuredClone(p);
    expect(moveClipToTrack(p, "b", 0)).toBe(false);
    expect(p).toEqual(before);
  });

  it("moveClipToTrack moves when free and prunes the emptied track", () => {
    const p = project([clip("a", 0, 1000)], [clip("b", 1000, 1000)]);
    expect(moveClipToTrack(p, "b", 0)).toBe(true);
    expect(tracks(p)).toEqual([["a", "b"]]);
  });

  it("setShowAudio adopts the audio duration only for empty or default timelines", () => {
    const audio = { id: "ast_a", kind: "audio", duration_ms: 30000 } as Asset;
    const p = project([clip("a", 0, 1000)]);
    setShowAudio(p, audio);
    expect(p.timeline.audio_asset_id).toBe("ast_a");
    expect(p.timeline.duration_ms).toBe(30000);
    p.timeline.duration_ms = 45000;
    setShowAudio(p, audio);
    expect(p.timeline.duration_ms).toBe(45000);
  });
});
