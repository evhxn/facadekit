import styles from "./Checklist.module.css";

export type CheckState = "ok" | "todo" | "problem" | "busy";

export interface CheckItem {
  label: string;
  state: CheckState;
  /** One short line: what is known, or what to do. */
  detail?: string;
}

const MARK: Record<CheckState, string> = { ok: "\u2713", todo: "", problem: "!", busy: "\u2026" };
const SPOKEN: Record<CheckState, string> = {
  ok: "done",
  todo: "not done yet",
  problem: "needs attention",
  busy: "in progress",
};

/** A short pass/fail list: what is ready and what still stands between the user and the goal. */
export default function Checklist({ title, items }: { title: string; items: CheckItem[] }) {
  return (
    <section className={styles.card} aria-label={title}>
      <h2 className={styles.heading}>{title}</h2>
      <ul className={styles.list}>
        {items.map((item) => (
          <li key={item.label} className={styles.item} data-state={item.state}>
            <span className={styles.mark} aria-hidden="true">
              {MARK[item.state]}
            </span>
            <span>
              <span className={styles.label}>{item.label}</span>
              <span className={styles.hidden}> ({SPOKEN[item.state]})</span>
              {item.detail ? <span className={styles.detail}>{item.detail}</span> : null}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
