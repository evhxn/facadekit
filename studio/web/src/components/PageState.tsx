import type { ReactNode } from "react";
import DiagnosticsButton from "./DiagnosticsButton";
import styles from "./PageState.module.css";

interface Props {
  kind: "loading" | "error" | "empty";
  title: string;
  children?: ReactNode;
  /** The one thing to do next (a link or button). */
  action?: ReactNode;
}

/** A whole page that has nothing to show yet: loading, failed, or waiting for an earlier step. */
export default function PageState({ kind, title, children, action }: Props) {
  return (
    <main className={styles.wrap}>
      <section
        className={styles.card}
        data-kind={kind}
        role={kind === "error" ? "alert" : "status"}
        aria-busy={kind === "loading" || undefined}
      >
        <h1 className={styles.title}>{title}</h1>
        {children ? <div className={styles.body}>{children}</div> : null}
        {action || kind === "error" ? (
          <div className={styles.actions}>
            {action}
            {kind === "error" ? <DiagnosticsButton /> : null}
          </div>
        ) : null}
      </section>
    </main>
  );
}
