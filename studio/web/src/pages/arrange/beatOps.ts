import { newId } from "../../api/types";
import type { Clip, Marker, Project } from "../../api/types";
import { findClip } from "./clipOps";

/** Times of `beat` markers with startMs <= t < endMs, ascending. */
export function beatsInRange(markers: Marker[], startMs: number, endMs: number): number[] {
  return markers
    .filter((m) => m.kind === "beat" && m.t_ms >= startMs && m.t_ms < endMs)
    .map((m) => m.t_ms)
    .sort((a, b) => a - b);
}

/**
 * Replace a clip by consecutive pieces cut at every N-th beat strictly inside it. Pieces stay on
 * the clip's track and keep all its properties; in_offset_ms advances by the elapsed time (for
 * looping clips too, so playback is continuous). Fades are kept only at the outer edges. The
 * first piece keeps the original id. Returns the piece ids, or [clipId] when nothing was cut.
 */
export function splitClipOnBeats(project: Project, clipId: string, everyN: number): string[] {
  const found = findClip(project, clipId);
  const n = Math.max(1, Math.floor(everyN));
  if (!found) return [clipId];
  const { track, clip } = found;
  const end = clip.start_ms + clip.duration_ms;
  const inside = beatsInRange(project.markers, clip.start_ms, end).filter((t) => t > clip.start_ms);
  const cuts = [...new Set(inside.filter((_, i) => (i + 1) % n === 0))];
  if (cuts.length === 0) return [clipId];
  const bounds = [clip.start_ms, ...cuts, end];
  const pieces: Clip[] = [];
  for (let i = 0; i < bounds.length - 1; i++) {
    const start = bounds[i]!;
    const last = i === bounds.length - 2;
    pieces.push({
      ...clip,
      id: i === 0 ? clip.id : newId("clp"),
      start_ms: start,
      duration_ms: bounds[i + 1]! - start,
      in_offset_ms: clip.in_offset_ms + (start - clip.start_ms),
      fade_in_ms: i === 0 ? clip.fade_in_ms : 0,
      fade_out_ms: last ? clip.fade_out_ms : 0,
    });
  }
  track.clips.splice(track.clips.indexOf(clip), 1, ...pieces);
  return pieces.map((p) => p.id);
}

/** Assign surfaces round-robin to the clips, in the order given. */
export function alternateSurfaces(project: Project, clipIds: string[], surfaceIds: string[]): void {
  if (surfaceIds.length === 0) return;
  clipIds.forEach((id, i) => {
    const found = findClip(project, id);
    if (found) found.clip.surface_id = surfaceIds[i % surfaceIds.length]!;
  });
}
