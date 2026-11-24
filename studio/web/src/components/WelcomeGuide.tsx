import { STEPS } from "./HelpDialog";
import styles from "./WelcomeGuide.module.css";

/**
 * The four steps on one screen. Open on a first visit (no projects yet), folded away once
 * there is a project. `onStart` takes the user to the new-project form.
 */
export default function WelcomeGuide({
  first,
  onStart,
  onTrySample,
}: {
  first: boolean;
  onStart: () => void;
  /** Takes the user to "Try a sample project" (no hardware needed). */
  onTrySample?: () => void;
}) {
  const steps = (
    <ol className={styles.steps}>
      {STEPS.map((s, i) => (
        <li key={s.name} className={styles.step}>
          <span className={styles.num} aria-hidden="true">
            {i + 1}
          </span>
          <span>
            <strong className={styles.name}>{s.name}</strong>
            <span className={styles.what}>{s.what}</span>
            <span className={styles.time}>Takes {s.time}.</span>
          </span>
        </li>
      ))}
    </ol>
  );
  if (!first) {
    return (
      <details className={styles.fold}>
        <summary>How FacadeKit works (4 steps)</summary>
        {steps}
      </details>
    );
  }
  return (
    <section className={styles.card} aria-labelledby="welcome-title">
      <h2 id="welcome-title" className={styles.title}>
        Welcome! Light up your house in four steps
      </h2>
      <p className={styles.lead}>
        You need a projector pointed at the house, a camera (a webcam or your phone) that sees it,
        and the videos or pictures you want to show. FacadeKit guides you through each step.
      </p>
      {steps}
      <button type="button" className={`primary ${styles.start}`} onClick={onStart}>
        Start your first project
      </button>
      {onTrySample ? (
        <p className={styles.lead}>
          No projector yet?{" "}
          <button type="button" className={styles.link} onClick={onTrySample}>
            See a sample show
          </button>{" "}
          on a simulated house first (no hardware needed).
        </p>
      ) : null}
    </section>
  );
}
