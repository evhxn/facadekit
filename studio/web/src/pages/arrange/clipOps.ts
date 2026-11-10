import { newId } from "../../api/types";
import type { Asset, Clip, Project, Track } from "../../api/types";

/** Half-open intervals: clips that only touch do not overlap. */
export function overlaps(a: Clip, b: Clip): boolean {
  return a.start_ms < b.start_ms + b.duration_ms && b.start_ms < a.start_ms + a.duration_ms;
}

export function findClip(project: Project, clipId: string): { track: Track; clip: Clip } | null {
  for (const track of project.timeline.tracks) {
    const clip = track.clips.find((c) => c.id === clipId);
    if (clip) return { track, clip };
  }
  return null;
}

function fits(track: Track, clip: Clip): boolean {
  return !track.clips.some((c) => c.id !== clip.id && overlaps(c, clip));
}

function newTrack(project: Project): Track {
  const track: Track = {
    id: newId("trk"),
    name: `Track ${project.timeline.tracks.length + 1}`,
    clips: [],
  };
  project.timeline.tracks.push(track);
  return track;
}

/** First track at index >= from where the clip fits; creates a new top track if none does. */
function placeFrom(project: Project, clip: Clip, from: number): void {
  const tracks = project.timeline.tracks;
  for (let i = Math.max(0, from); i < tracks.length; i++) {
    const t = tracks[i];
    if (t && fits(t, clip)) {
      t.clips.push(clip);
      return;
    }
  }
  newTrack(project).clips.push(clip);
}

export function addClip(project: Project, clip: Clip, trackIndex: number | "new"): void {
  if (trackIndex === "new") {
    newTrack(project).clips.push(clip);
    return;
  }
  placeFrom(project, clip, trackIndex);
}

export function updateClip(project: Project, clipId: string, patch: Partial<Clip>): void {
  const found = findClip(project, clipId);
  if (!found) return;
  const next: Clip = { ...found.clip, ...patch, id: found.clip.id };
  if (fits(found.track, next)) {
    found.track.clips[found.track.clips.indexOf(found.clip)] = next;
    return;
  }
  const from = project.timeline.tracks.indexOf(found.track);
  found.track.clips.splice(found.track.clips.indexOf(found.clip), 1);
  placeFrom(project, next, from + 1);
  pruneEmptyTracks(project);
}

export function moveClipToTrack(
  project: Project,
  clipId: string,
  trackIndex: number | "new",
): boolean {
  const found = findClip(project, clipId);
  if (!found) return false;
  const tracks = project.timeline.tracks;
  if (trackIndex !== "new") {
    const target = tracks[trackIndex];
    if (!target) return false;
    if (target === found.track) return true;
    if (!fits(target, found.clip)) return false;
    found.track.clips.splice(found.track.clips.indexOf(found.clip), 1);
    target.clips.push(found.clip);
  } else {
    if (found.track.clips.length === 1 && tracks[tracks.length - 1] === found.track) return true;
    found.track.clips.splice(found.track.clips.indexOf(found.clip), 1);
    newTrack(project).clips.push(found.clip);
  }
  pruneEmptyTracks(project);
  return true;
}

function pruneEmptyTracks(project: Project): void {
  const tracks = project.timeline.tracks;
  const kept = tracks.filter((t) => t.clips.length > 0);
  if (kept.length === 0) {
    const first = tracks[0];
    project.timeline.tracks = first ? [first] : [];
  } else {
    project.timeline.tracks = kept;
  }
}

export function removeClip(project: Project, clipId: string): void {
  const found = findClip(project, clipId);
  if (!found) return;
  found.track.clips.splice(found.track.clips.indexOf(found.clip), 1);
  pruneEmptyTracks(project);
}

/** Set the show audio; adopt the audio's duration when the timeline is empty or still the default. */
export function setShowAudio(project: Project, asset: Asset | null): void {
  const tl = project.timeline;
  tl.audio_asset_id = asset ? asset.id : null;
  if (!asset || !asset.duration_ms || asset.duration_ms <= 0) return;
  const noClips = tl.tracks.every((t) => t.clips.length === 0);
  if (noClips || tl.duration_ms === 60000) tl.duration_ms = asset.duration_ms;
}

export const MIN_CLIP_MS = 100;

/**
 * Move clips by deltaMs in time and deltaTracks in track index (positive = towards the top
 * layer). Moving above the top creates new tracks. All-or-nothing: returns false and leaves
 * the project untouched if any clip would start before 0, go below the bottom track or overlap.
 */
export function moveClips(
  project: Project,
  ids: string[],
  deltaMs: number,
  deltaTracks: number,
): boolean {
  const tracks = project.timeline.tracks;
  const moving = new Set(ids);
  const plan: { clip: Clip; target: number }[] = [];
  for (const id of moving) {
    const found = findClip(project, id);
    if (!found) return false;
    const target = tracks.indexOf(found.track) + deltaTracks;
    const start = found.clip.start_ms + deltaMs;
    if (target < 0 || start < 0) return false;
    plan.push({ clip: { ...found.clip, start_ms: start }, target });
  }
  const resident = (i: number): Clip[] => (tracks[i]?.clips ?? []).filter((c) => !moving.has(c.id));
  for (let i = 0; i < plan.length; i++) {
    const a = plan[i]!;
    if (resident(a.target).some((c) => overlaps(c, a.clip))) return false;
    for (let j = i + 1; j < plan.length; j++) {
      const b = plan[j]!;
      if (a.target === b.target && overlaps(a.clip, b.clip)) return false;
    }
  }
  for (const id of moving) {
    const found = findClip(project, id);
    if (found) found.track.clips.splice(found.track.clips.indexOf(found.clip), 1);
  }
  while (tracks.length <= Math.max(...plan.map((p) => p.target))) newTrack(project);
  for (const { clip, target } of plan) tracks[target]!.clips.push(clip);
  pruneEmptyTracks(project);
  return true;
}

/**
 * Trim one edge of a clip. Start trims keep the content in place (start_ms, duration_ms and
 * in_offset_ms change together); the result is clamped to MIN_CLIP_MS, to neighbouring clips,
 * to time 0 / in_offset 0 and, for non-looping clips with a known source length, to the source.
 */
export function trimClip(
  project: Project,
  clipId: string,
  edge: "start" | "end",
  newMs: number,
  assetDurationMs: number | null,
): void {
  const found = findClip(project, clipId);
  if (!found) return;
  const { track, clip } = found;
  const others = track.clips.filter((c) => c.id !== clip.id);
  const end = clip.start_ms + clip.duration_ms;
  if (edge === "start") {
    const prevEnd = Math.max(
      0,
      ...others
        .filter((c) => c.start_ms + c.duration_ms <= clip.start_ms)
        .map((c) => c.start_ms + c.duration_ms),
    );
    const lowest = Math.max(prevEnd, clip.start_ms - clip.in_offset_ms);
    const start = Math.round(Math.min(Math.max(newMs, lowest), end - MIN_CLIP_MS));
    const delta = start - clip.start_ms;
    clip.start_ms = start;
    clip.duration_ms = end - start;
    clip.in_offset_ms += delta;
  } else {
    const nextStart = Math.min(
      Infinity,
      ...others.filter((c) => c.start_ms >= end).map((c) => c.start_ms),
    );
    let highest = nextStart;
    if (!clip.loop && assetDurationMs !== null) {
      highest = Math.min(highest, clip.start_ms + Math.max(0, assetDurationMs - clip.in_offset_ms));
    }
    const newEnd = Math.round(Math.min(Math.max(newMs, clip.start_ms + MIN_CLIP_MS), highest));
    clip.duration_ms = Math.max(MIN_CLIP_MS, newEnd - clip.start_ms);
  }
}

/**
 * Duplicate clips directly after the selection (shifted by the selection's span), each on the
 * lowest track at or above its own where it fits. Returns the new clip ids.
 */
export function duplicateClips(project: Project, ids: string[]): string[] {
  const originals: { clip: Clip; index: number }[] = [];
  for (const id of ids) {
    const found = findClip(project, id);
    if (found) {
      originals.push({
        clip: { ...found.clip },
        index: project.timeline.tracks.indexOf(found.track),
      });
    }
  }
  if (originals.length === 0) return [];
  const span =
    Math.max(...originals.map((o) => o.clip.start_ms + o.clip.duration_ms)) -
    Math.min(...originals.map((o) => o.clip.start_ms));
  const out: string[] = [];
  for (const { clip, index } of originals) {
    const copy: Clip = { ...clip, id: newId("clp"), start_ms: clip.start_ms + span };
    placeFrom(project, copy, index);
    out.push(copy.id);
  }
  return out;
}
