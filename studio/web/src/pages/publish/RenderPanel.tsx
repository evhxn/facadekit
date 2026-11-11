import { useEffect, useRef, useState } from "react";
import { ApiError, cancelJob, renderFileUrl, startRender } from "../../api/client";
import { waitForJob } from "../../api/events";
import { userMessage } from "../../api/errors";
import type { Job, Project, RenderInfo } from "../../api/types";
import ErrorBanner from "../../components/ErrorBanner";
import JobProgress from "../../components/JobProgress";
import { useProjectStore } from "../../state/projectStore";
import styles from "./Panels.module.css";

const PRESETS: { id: string; label: string }[] = [
  { id: "pi3-1080p30", label: "Raspberry Pi – best quality (1080p)" },
  { id: "pi3-720p30", label: "Raspberry Pi – lighter (720p). Use if playback stutters." },
  { id: "preview-540p", label: "Quick preview (low quality)" },
];

export interface RenderPanelProps {
  project: Project;
  renders: RenderInfo[];
  selectedId: string | null;
  onSelect: (renderId: string) => void;
  onRendered: (renderId: string) => Promise<void>;
}

export default function RenderPanel({
  project,
  renders,
  selectedId,
  onSelect,
  onRendered,
}: RenderPanelProps) {
  const [preset, setPreset] = useState("pi3-1080p30");
  const [includeAudio, setIncludeAudio] = useState(true);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const hasAudio = project.timeline.audio_asset_id !== null;
  const warnings: string[] = [];
  if (!project.timeline.tracks.some((t) => t.clips.length > 0)) warnings.push("The show is empty.");
  if (project.surfaces.length === 0) warnings.push("There are no surfaces yet.");
  if (!project.calibration_id) warnings.push("This project has no calibration.");

  const render = async () => {
    setError(null);
    setBusy(true);
    setJob(null);
    try {
      await useProjectStore.getState().saveNow();
      const { job_id } = await startRender(project.id, {
        preset,
        include_audio: hasAudio && includeAudio,
        kind: "show",
      });
      const final = await waitForJob(job_id, (j) => mounted.current && setJob(j));
      if (!mounted.current) return;
      setJob(final);
      if (final.state === "succeeded") {
        const rid = typeof final.result?.render_id === "string" ? final.result.render_id : "";
        await onRendered(rid);
        if (mounted.current) setJob(null);
      } else if (final.state === "failed") {
        setError(
          final.error ? userMessage(final.error.code, final.error.message) : "Rendering failed.",
        );
      } else if (final.state === "cancelled") {
        setJob(null);
      }
    } catch (e) {
      if (mounted.current)
        setError(e instanceof ApiError ? userMessage(e.code, e.message) : "Rendering failed.");
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  return (
    <section className={styles.panel}>
      <h2>Render</h2>
      {warnings.map((w) => (
        <p key={w} className={styles.warn}>
          {w}
        </p>
      ))}
      <div className={styles.row}>
        <label>
          Quality{" "}
          <select value={preset} onChange={(e) => setPreset(e.target.value)} disabled={busy}>
            {PRESETS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={hasAudio && includeAudio}
            disabled={!hasAudio || busy}
            onChange={(e) => setIncludeAudio(e.target.checked)}
          />{" "}
          Include audio
        </label>
        {hasAudio ? null : <span className={styles.note}>The timeline has no audio.</span>}
        <button type="button" onClick={() => void render()} disabled={busy} className="primary">
          Render show
        </button>
      </div>
      {job ? (
        <JobProgress job={job} onCancel={() => void cancelJob(job.id).catch(() => {})} />
      ) : null}
      {error ? <ErrorBanner message={error} /> : null}
      {renders.length > 0 ? (
        <ul className={styles.list} aria-label="Renders">
          {renders.map((r) => (
            <li key={r.render_id}>
              <input
                type="radio"
                name="render"
                aria-label={`Publish ${r.filename}`}
                checked={selectedId === r.render_id}
                onChange={() => onSelect(r.render_id)}
              />
              <span>{r.filename}</span>
              <span>{(r.size_bytes / (1024 * 1024)).toFixed(1)} MB</span>
              <span>{new Date(r.modified_at).toLocaleString()}</span>
              <a href={renderFileUrl(project.id, r.render_id)} target="_blank" rel="noreferrer">
                Preview
              </a>
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.note}>No renders yet.</p>
      )}
    </section>
  );
}
