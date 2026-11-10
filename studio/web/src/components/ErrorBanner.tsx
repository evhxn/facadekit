import type { ReactNode } from "react";
import styles from "./ErrorBanner.module.css";

export default function ErrorBanner({
  message,
  actions,
}: {
  message: string;
  actions?: ReactNode;
}) {
  return (
    <div className={styles.banner} role="alert">
      <p className={styles.message}>{message}</p>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </div>
  );
}
