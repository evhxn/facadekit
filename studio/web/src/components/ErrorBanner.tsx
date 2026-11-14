import type { ReactNode } from "react";
import DiagnosticsButton from "./DiagnosticsButton";
import styles from "./ErrorBanner.module.css";

export default function ErrorBanner({
  message,
  actions,
}: {
  message: string;
  actions?: ReactNode;
}) {
  return (
    <div className={styles.banner}>
      <div role="alert">
        <p className={styles.message}>{message}</p>
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
      {/* Outside the alert: screen readers announce the problem, not the reporting tool. */}
      <div className={styles.actions}>
        <DiagnosticsButton />
      </div>
    </div>
  );
}
