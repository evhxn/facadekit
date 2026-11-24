import { useToasts, type Toast } from "./toast";
import styles from "./Toaster.module.css";

/** The toasts, bottom of the screen. Success and info are polite; errors are alerts. */
export default function Toaster() {
  const toasts = useToasts((s) => s.toasts);
  const dismiss = useToasts((s) => s.dismiss);

  const item = (t: Toast) => (
    <div key={t.id} className={styles.toast} data-kind={t.kind}>
      <span className={styles.message}>{t.message}</span>
      {t.action ? (
        <button
          type="button"
          className={styles.action}
          onClick={() => {
            t.action?.run();
            dismiss(t.id);
          }}
        >
          {t.action.label}
        </button>
      ) : null}
      <button
        type="button"
        className={styles.close}
        aria-label="Close notification"
        onClick={() => dismiss(t.id)}
      >
        ×
      </button>
    </div>
  );

  return (
    <div className={styles.region} aria-label="Notifications">
      <div role="status" aria-live="polite" className={styles.stack}>
        {toasts.filter((t) => t.kind !== "error").map(item)}
      </div>
      <div role="alert" className={styles.stack}>
        {toasts.filter((t) => t.kind === "error").map(item)}
      </div>
    </div>
  );
}
