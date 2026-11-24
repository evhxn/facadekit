import { useProjectStore } from "../state/projectStore";
import ErrorBanner from "./ErrorBanner";
import styles from "./SaveStatus.module.css";

/** "Saving…" / "All changes saved" / "Not saved", for the project that is open. */
export default function SaveStatus() {
  const loaded = useProjectStore((s) => s.project !== null);
  const dirty = useProjectStore((s) => s.dirty);
  const saving = useProjectStore((s) => s.saving);
  const error = useProjectStore((s) => s.error);
  if (!loaded) return null;
  const state = error && dirty ? "failed" : saving || dirty ? "saving" : "saved";
  const text =
    state === "failed" ? "Not saved" : state === "saving" ? "Saving…" : "All changes saved";
  return (
    // Not a live region: it changes on every edit. A failed save is announced by SaveProblem.
    <span className={styles.status} data-state={state} data-testid="save-status">
      <span className={styles.dot} aria-hidden="true" />
      {text}
    </span>
  );
}

/**
 * A save that failed, said where the user is looking, with what to do. The editors save in
 * the background; without this a failed save was silent.
 */
export function SaveProblem() {
  const error = useProjectStore((s) => s.error);
  const dirty = useProjectStore((s) => s.dirty);
  if (!error) return null;
  return (
    <ErrorBanner
      message={error}
      todo={
        dirty
          ? "Your latest changes are only in this window. Check that FacadeKit is still running, then press Try again."
          : "The project was reloaded with the saved version. Make your last change again."
      }
      actions={
        dirty ? (
          <button
            type="button"
            className="primary"
            onClick={() => void useProjectStore.getState().saveNow()}
          >
            Try again
          </button>
        ) : (
          <button type="button" onClick={() => useProjectStore.setState({ error: null })}>
            OK
          </button>
        )
      }
    />
  );
}
