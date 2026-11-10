import { describe, expect, it } from "vitest";
import { makeProject } from "../../api/client.test";
import type { Asset, Clip, Project } from "../../api/types";
import {
  addClip,
  duplicateClips,
  findClip,
  moveClips,
  moveClipToTrack,
  overlaps,
  removeClip,
  setShowAudio,
  trimClip,
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

describe("moveClips", () => {
  const names = (p: Project) => p.timeline.tracks.map((t) => t.clips.map((c) => c.id));

  it("moves in time and between tracks", () => {
    const p = project([clip("a", 0, 1000)], [clip("b", 0, 1000)]);
    expect(moveClips(p, ["a"], 2000, 1)).toBe(true);
    expect(findClip(p, "a")?.clip.start_ms).toBe(2000);
    expect(names(p)).toEqual([["b", "a"]]);
  });

  it("rejects an overlapping move atomically", () => {
    const p = project([clip("a", 0, 1000), clip("b", 1500, 1000)], [clip("c", 0, 1000)]);
    const before = structuredClone(p);
    expect(moveClips(p, ["c", "a"], 1000, 0)).toBe(false);
    expect(p).toEqual(before);
  });

  it("rejects negative starts and moves below the bottom track", () => {
    const p = project([clip("a", 500, 1000)]);
    expect(moveClips(p, ["a"], -600, 0)).toBe(false);
    expect(moveClips(p, ["a"], 0, -1)).toBe(false);
  });

  it("creates a track when moving above the top", () => {
    const p = project([clip("a", 0, 1000)]);
    expect(moveClips(p, ["a"], 0, 1)).toBe(true);
    expect(p.timeline.tracks).toHaveLength(1);
    const q = project([clip("a", 0, 1000), clip("b", 2000, 1000)]);
    expect(moveClips(q, ["a"], 0, 1)).toBe(true);
    expect(names(q)).toEqual([["b"], ["a"]]);
  });

  it("lets a selection move over its own former positions", () => {
    const p = project([clip("a", 0, 1000), clip("b", 1000, 1000)]);
    expect(moveClips(p, ["a", "b"], 1000, 0)).toBe(true);
    expect(names(p)).toEqual([["a", "b"]]);
    expect(findClip(p, "a")?.clip.start_ms).toBe(1000);
  });
});

describe("trimClip", () => {
  it("start trim keeps the end fixed and shifts in_offset", () => {
    const p = project([clip("a", 1000, 4000)]);
    trimClip(p, "a", "start", 2000, null);
    const c = findClip(p, "a")!.clip;
    expect(c.start_ms).toBe(2000);
    expect(c.duration_ms).toBe(3000);
    expect(c.start_ms + c.duration_ms).toBe(5000);
    expect(c.in_offset_ms).toBe(1000);
  });

  it("enforces the minimum duration and cannot reveal content before the source start", () => {
    const p = project([clip("a", 1000, 4000)]);
    trimClip(p, "a", "start", 9000, null);
    expect(findClip(p, "a")!.clip.duration_ms).toBe(100);
    trimClip(p, "a", "start", 0, null);
    const c = findClip(p, "a")!.clip;
    expect(c.in_offset_ms).toBe(0);
    expect(c.start_ms).toBe(1000);
  });

  it("right trim clamps to the source length for non-looping clips", () => {
    const p = project([{ ...clip("a", 0, 1000), loop: false, in_offset_ms: 500 }]);
    trimClip(p, "a", "end", 99999, 8000);
    expect(findClip(p, "a")!.clip.duration_ms).toBe(7500);
    const q = project([clip("b", 0, 1000)]);
    trimClip(q, "b", "end", 20000, 8000);
    expect(findClip(q, "b")!.clip.duration_ms).toBe(20000);
  });

  it("does not trim into a neighbour", () => {
    const p = project([clip("a", 0, 1000), clip("b", 2000, 1000)]);
    trimClip(p, "a", "end", 5000, null);
    expect(findClip(p, "a")!.clip.duration_ms).toBe(2000);
    trimClip(p, "b", "start", 0, null);
    expect(findClip(p, "b")!.clip.start_ms).toBe(2000);
  });
});

describe("duplicateClips", () => {
  it("returns new ids placed after the originals without overlaps", () => {
    const p = project([clip("a", 0, 1000), clip("b", 1000, 1000)]);
    const ids = duplicateClips(p, ["a", "b"]);
    expect(ids).toHaveLength(2);
    expect(ids).not.toContain("a");
    for (const t of p.timeline.tracks) {
      for (const x of t.clips) {
        for (const y of t.clips) if (x !== y) expect(overlaps(x, y)).toBe(false);
      }
    }
    expect(findClip(p, ids[0]!)?.clip.start_ms).toBe(2000);
    expect(p.timeline.tracks.flatMap((t) => t.clips)).toHaveLength(4);
  });

  it("moves to a free track when the slot after is taken", () => {
    const p = project([clip("a", 0, 1000), clip("b", 1000, 1000)]);
    const ids = duplicateClips(p, ["a"]);
    expect(p.timeline.tracks).toHaveLength(2);
    expect(findClip(p, ids[0]!)?.track).toBe(p.timeline.tracks[1]);
  });
});
