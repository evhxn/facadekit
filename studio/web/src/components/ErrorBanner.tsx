import type { ReactNode } from "react";
import DiagnosticsButton from "./DiagnosticsButton";
import styles from "./ErrorBanner.module.css";

export default function ErrorBanner({
  message,
  actions,
  todo,
}: {
  message: string;
  actions?: ReactNode;
  /** One line: what the user can do about it. */
  todo?: string;
}) {
  return (
    <div className={styles.banner}>
      <div role="alert">
        <p className={styles.message}>{message}</p>
        {todo ? (
          <p className={styles.todo}>
            <strong>What to do:</strong> {todo}
          </p>
        ) : null}
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
      {/* Outside the alert: screen readers announce the problem, not the reporting tool. */}
      <div className={styles.actions}>
        <DiagnosticsButton />
      </div>
    </div>
  );
}
