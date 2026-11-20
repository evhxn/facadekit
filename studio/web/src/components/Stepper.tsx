import styles from "./Stepper.module.css";

export interface Step {
  title: string;
  /** Finished steps show a tick. */
  done: boolean;
}

interface Props {
  steps: Step[];
  /** 1-based. */
  current: number;
  /** Jump to a step; only steps before the current one, and finished ones, can be chosen. */
  onSelect?: (step: number) => void;
}

/** Where the user is in a short wizard, with the ways back. */
export default function Stepper({ steps, current, onSelect }: Props) {
  const title = steps[current - 1]?.title ?? "";
  return (
    <nav className={styles.wrap} aria-label="Setup steps">
      <ol className={styles.list}>
        {steps.map((step, i) => {
          const n = i + 1;
          const state = n === current ? "current" : step.done ? "done" : "todo";
          const reachable = onSelect !== undefined && n !== current && (n < current || step.done);
          const body = (
            <>
              <span className={styles.dot} aria-hidden="true">
                {step.done && n !== current ? "\u2713" : n}
              </span>
              <span className={styles.title}>{step.title}</span>
            </>
          );
          return (
            <li key={step.title} className={styles.item} data-state={state}>
              {reachable ? (
                <button type="button" className={styles.jump} onClick={() => onSelect(n)}>
                  {body}
                </button>
              ) : (
                <span className={styles.static} aria-current={n === current ? "step" : undefined}>
                  {body}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      <p className={styles.where}>
        Step {current} of {steps.length}: {title}
      </p>
    </nav>
  );
}
