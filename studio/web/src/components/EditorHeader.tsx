import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import styles from "./EditorHeader.module.css";

interface Props {
  title: string;
  /** One line: what this step is for. */
  help: ReactNode;
  /** The next step, when there is one worth pointing at. */
  next?: { to: string; label: string; ready: boolean; hint?: string };
  /** Small controls that belong to the whole page (toggles, remote). */
  tools?: ReactNode;
  /** What to do now, in one short line: changes with the state of the page. */
  todo?: ReactNode;
}

/** The top line of an editor page: what it is for, page tools, and the way on. */
export default function EditorHeader({ title, help, next, tools, todo }: Props) {
  return (
    <header className={styles.header}>
      <div className={styles.text}>
        <h1 className={styles.title}>{title}</h1>
        <p className={styles.help}>{help}</p>
        {todo ? (
          <p className={styles.todo} data-testid="next-step">
            <span className={styles.todoLabel}>Next step:</span> {todo}
          </p>
        ) : null}
      </div>
      {tools ? <div className={styles.tools}>{tools}</div> : null}
      {next ? (
        <div className={styles.next}>
          <Link
            to={next.to}
            className={`${styles.nextLink} ${next.ready ? styles.ready : ""}`}
            aria-describedby={next.hint && !next.ready ? "next-hint" : undefined}
          >
            {next.label} →
          </Link>
          {next.hint && !next.ready ? (
            <span id="next-hint" className={styles.hint}>
              {next.hint}
            </span>
          ) : null}
        </div>
      ) : null}
    </header>
  );
}
