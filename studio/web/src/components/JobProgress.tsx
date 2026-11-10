import { isFinalJobState, type Job } from "../api/types";
import styles from "./JobProgress.module.css";

export default function JobProgress({ job, onCancel }: { job: Job; onCancel: () => void }) {
  const percent = Math.round(Math.min(1, Math.max(0, job.progress)) * 100);
  return (
    <div className={styles.wrap}>
      <progress className={styles.bar} max={100} value={percent} aria-label="Progress" />
      <p className={styles.message}>{job.message}</p>
      {isFinalJobState(job.state) ? null : (
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      )}
    </div>
  );
}
