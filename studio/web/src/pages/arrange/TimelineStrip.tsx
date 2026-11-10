import type { Project } from "../../api/types";
import { assetName } from "./ClipList";
import { useTransportTime, type Transport } from "./transport";
import styles from "./TimelineStrip.module.css";

function rulerStep(durationMs: number): number {
  const secs = durationMs / 1000;
  for (const step of [1, 2, 5, 10, 15, 30, 60, 120, 300]) {
    if (secs / step <= 20) return step * 1000;
  }
  return 600000;
}

export default function TimelineStrip({
  project,
  transport,
  selectedId,
  onSelect,
}: {
  project: Project;
  transport: Transport;
  selectedId: string | null;
  onSelect: (clipId: string) => void;
}) {
  const t = useTransportTime(transport);
  const duration = Math.max(1, project.timeline.duration_ms);
  const pct = (ms: number) => `${(Math.min(Math.max(ms, 0), duration) / duration) * 100}%`;
  const step = rulerStep(duration);
  const ticks: number[] = [];
  for (let ms = 0; ms <= duration; ms += step) ticks.push(ms);
  const rows = [...project.timeline.tracks].reverse();

  return (
    <div
      className={styles.strip}
      aria-label="Timeline strip"
      onClick={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        if (rect.width <= 0) return;
        transport.seek(((e.clientX - rect.left) / rect.width) * duration);
      }}
    >
      <div className={styles.ruler}>
        {ticks.map((ms) => (
          <span key={ms} className={styles.tick} style={{ left: pct(ms) }}>
            {ms / 1000}s
          </span>
        ))}
      </div>
      {rows.map((track) => (
        <div key={track.id} className={styles.lane} title={track.name}>
          {track.clips.map((clip) => (
            <button
              key={clip.id}
              type="button"
              className={`${styles.bar} ${clip.id === selectedId ? styles.selected : ""}`}
              style={{
                left: pct(clip.start_ms),
                width: `${(Math.min(clip.duration_ms, duration - clip.start_ms) / duration) * 100}%`,
              }}
              onClick={(e) => {
                e.stopPropagation();
                onSelect(clip.id);
              }}
            >
              {assetName(project, clip.asset_id)}
            </button>
          ))}
        </div>
      ))}
      <div className={styles.playhead} style={{ left: pct(t) }} data-testid="playhead" />
    </div>
  );
}
