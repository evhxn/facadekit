import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, cancelJob, createSampleProject, type SampleTheme } from "../../api/client";
import { userMessage } from "../../api/errors";
import { waitForJob } from "../../api/events";
import type { Job } from "../../api/types";
import ErrorBanner from "../../components/ErrorBanner";
import JobProgress from "../../components/JobProgress";
import styles from "./TrySample.module.css";

const THEMES: { id: SampleTheme; label: string }[] = [
  { id: "halloween", label: "Halloween" },
  { id: "winter", label: "Winter" },
];

/** "Try a sample project": a simulated house with a ready-made show, no hardware needed. */
export default function TrySample() {
  const navigate = useNavigate();
  const [theme, setTheme] = useState<SampleTheme>("halloween");
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  useEffect(
    () => () => {
      mounted.current = false;
    },
    [],
  );

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const { job_id, project_id } = await createSampleProject(theme);
      const final = await waitForJob(job_id, (j) => mounted.current && setJob(j));
      if (!mounted.current) return;
      if (final.state === "succeeded") {
        navigate(`/p/${project_id}/arrange`);
        return;
      }
      setJob(null);
      setBusy(false);
      if (final.state === "failed") {
        setError(
          final.error
            ? userMessage(final.error.code, final.error.message)
            : "Could not make the sample. Try again.",
        );
      }
    } catch (e) {
      if (!mounted.current) return;
      setJob(null);
      setBusy(false);
      setError(
        e instanceof ApiError
          ? userMessage(e.code, e.message)
          : "Could not make the sample. Check that FacadeKit is running, then try again.",
      );
    }
  };

  return (
    <section className={styles.box} aria-labelledby="try-sample">
      <h2 id="try-sample">Try a sample project</h2>
      <p className={styles.help}>
        No projector, camera or Pi needed. FacadeKit scans a <strong>simulated house</strong>, marks
        its garage door and two windows, and lays out a 12-second show made only from built-in
        effects. You can change it, render it and go on to Publish.
      </p>
      {error ? <ErrorBanner message={error} /> : null}
      {job ? (
        <JobProgress job={job} onCancel={() => void cancelJob(job.id).catch(() => {})} />
      ) : (
        <div className={styles.row}>
          <label>
            Theme
            <select
              id="try-sample-theme"
              value={theme}
              onChange={(e) => setTheme(e.target.value as SampleTheme)}
            >
              {THEMES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <button type="button" disabled={busy} onClick={() => void start()}>
            {busy ? "Making the sample…" : "Try a sample project"}
          </button>
        </div>
      )}
    </section>
  );
}
