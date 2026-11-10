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
