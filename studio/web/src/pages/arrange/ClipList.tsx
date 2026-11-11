import type { Asset, Clip, Project } from "../../api/types";
import { SecondsInput } from "./ClipForm";
import styles from "./ClipList.module.css";

export interface ClipRow {
  clip: Clip;
  trackIndex: number;
}

/** Clips sorted by track, then start time. */
export function sortedClips(project: Project): ClipRow[] {
  const rows: ClipRow[] = [];
  project.timeline.tracks.forEach((t, trackIndex) => {
    for (const clip of t.clips) rows.push({ clip, trackIndex });
  });
  return rows.sort((a, b) => a.trackIndex - b.trackIndex || a.clip.start_ms - b.clip.start_ms);
}

export function surfaceName(project: Project, surfaceId: string): string {
  if (surfaceId === "canvas") return "Whole frame";
  return project.surfaces.find((s) => s.id === surfaceId)?.name ?? "Missing surface";
}

export function assetName(project: Project, assetId: string | null): string {
  return project.assets.find((a) => a.id === assetId)?.original_name ?? "Missing file";
}

/** Label for a clip: its file name, or the effect type for generator clips. */
export function clipLabel(project: Project, clip: Clip): string {
  if (clip.generator) return `Effect: ${clip.generator.type}`;
  return assetName(project, clip.asset_id);
}

const sec = (ms: number) => (ms / 1000).toFixed(1);

export default function ClipList({
  project,
  selectedId,
  onSelect,
  onAdd,
  onDuration,
  onShowAudio,
  onFindBeats,
  beats,
}: {
  project: Project;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAdd: () => void;
  onDuration: (ms: number) => void;
  onShowAudio: (asset: Asset | null) => void;
  onFindBeats: () => void;
  /** Beat detection state: progress 0..1 while running, an error message when it failed. */
  beats: { busy: boolean; progress: number; error: string | null };
}) {
  const rows = sortedClips(project);
  const eligible = project.assets.filter(
    (a) => a.kind === "audio" || (a.kind === "video" && a.has_audio),
  );
  return (
    <section className={styles.list} aria-label="Clips">
      <h2>Timeline</h2>
      <div className={styles.settings}>
        <SecondsInput
          label="Timeline duration (s)"
          valueMs={project.timeline.duration_ms}
          minExclusive
          onCommit={onDuration}
        />
        <label>
          Show audio
          <select
            value={project.timeline.audio_asset_id ?? ""}
            onChange={(e) =>
              onShowAudio(project.assets.find((a) => a.id === e.target.value) ?? null)
            }
          >
            <option value="">None</option>
            {eligible.map((a) => (
              <option key={a.id} value={a.id}>
                {a.original_name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={onFindBeats}
          disabled={beats.busy || project.timeline.audio_asset_id === null}
        >
          {beats.busy ? `Finding beats… ${Math.round(beats.progress * 100)} %` : "Find beats"}
        </button>
      </div>
      {project.timeline.audio_asset_id === null ? (
        <p className={styles.hint}>To find beats, pick a song or video under &quot;Show audio&quot;.</p>
      ) : null}
      {beats.error ? <p role="alert">{beats.error}</p> : null}
      <div className={styles.bar}>
        <h3>Clips</h3>
        <button type="button" onClick={onAdd}>
          Add clip
        </button>
      </div>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Layer</th>
            <th>File / effect</th>
            <th>Surface</th>
            <th>Start</th>
            <th>Length</th>
            <th>Loop</th>
            <th>Blend</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={7}>
                No clips yet. Press &quot;Add clip&quot; to put a video, picture or effect on the
                timeline.
              </td>
            </tr>
          ) : null}
          {rows.map(({ clip, trackIndex }) => (
            <tr
              key={clip.id}
              className={clip.id === selectedId ? styles.selected : undefined}
              aria-selected={clip.id === selectedId}
              onClick={() => onSelect(clip.id)}
            >
              <td>{trackIndex + 1}</td>
              <td>{clipLabel(project, clip)}</td>
              <td>{surfaceName(project, clip.surface_id)}</td>
              <td>{sec(clip.start_ms)} s</td>
              <td>{sec(clip.duration_ms)} s</td>
              <td>{clip.loop ? "Yes" : "No"}</td>
              <td>{clip.blend}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
