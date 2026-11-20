import { useEffect, useRef, useState } from "react";
import { isFinalJobState, type Job } from "../api/types";
import styles from "./JobProgress.module.css";

/** "about 40 s", "about 2 min"; null while there is too little to go on. */
export function remainingText(progress: number, elapsedS: number): string | null {
  if (progress < 0.05 || progress >= 1 || elapsedS < 2) return null;
  const left = (elapsedS * (1 - progress)) / progress;
  if (left < 5) return "a few seconds left";
  if (left < 90) return `about ${Math.round(left / 5) * 5} s left`;
  return `about ${Math.round(left / 60)} min left`;
}

export default function JobProgress({ job, onCancel }: { job: Job; onCancel: () => void }) {
  const fraction = Math.min(1, Math.max(0, job.progress));
  const percent = Math.round(fraction * 100);
  const started = useRef<{ id: string; at: number } | null>(null);
  if (started.current?.id !== job.id) started.current = { id: job.id, at: Date.now() };
  const [, tick] = useState(0);
  const final = isFinalJobState(job.state);

  useEffect(() => {
    if (final) return;
    const timer = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [final]);

  const elapsed = (Date.now() - started.current.at) / 1000;
  const remaining = final || job.state === "queued" ? null : remainingText(fraction, elapsed);
  return (
    <div className={styles.wrap}>
      <div className={styles.head}>
        <p className={styles.message} aria-live="polite">
          {job.state === "queued" ? "Waiting for the job before this one…" : job.message}
        </p>
        <span className={styles.percent}>{percent}%</span>
      </div>
      <progress className={styles.bar} max={100} value={percent} aria-label="Progress" />
      <div className={styles.foot}>
        <span className={styles.remaining}>{remaining ?? "\u00a0"}</span>
        {final ? null : (
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}
