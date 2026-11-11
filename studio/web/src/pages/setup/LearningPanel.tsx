import { useCallback, useEffect, useState } from "react";
import { ApiError, forgetLearning, getLearning } from "../../api/client";
import { userMessage } from "../../api/errors";
import type { LearningSummary } from "../../api/types";
import ErrorBanner from "../../components/ErrorBanner";
import styles from "../SetupPage.module.css";

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function errText(e: unknown): string {
  return e instanceof ApiError
    ? userMessage(e.code, e.message)
    : "Something went wrong. Check that FacadeKit is running, then try again.";
}

/** "What FacadeKit has learned": scan memory in plain words, with a Forget button. */
export default function LearningPanel() {
  const [summary, setSummary] = useState<LearningSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setSummary(await getLearning());
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const forget = async () => {
    setBusy(true);
    try {
      await forgetLearning();
      setConfirming(false);
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label="What FacadeKit has learned">
      <h4>What FacadeKit has learned</h4>
      {error ? (
        <ErrorBanner
          message={error}
          actions={
            <button type="button" onClick={() => void load()}>
              Try again
            </button>
          }
        />
      ) : null}
      {summary === null ? (
        error ? null : (
          <p className={styles.note}>Loading…</p>
        )
      ) : !summary.enabled ? (
        <p className={styles.note}>
          Learning is turned off, so scans always use the standard settings. Nothing is being
          remembered. To turn it on, restart FacadeKit without the learning-off setting.
        </p>
      ) : summary.scans === 0 ? (
        <p className={styles.note}>
          Nothing yet. After each scan, FacadeKit remembers which settings worked on your camera and
          tries them first next time. Run a scan and tell it how it went to get started.
        </p>
      ) : (
        <>
          <p>
            FacadeKit has learned from {plural(summary.scans, "scan", "scans")}. It tries the
            settings that worked best first.
          </p>
          <ul>
            {summary.strategies.map((s) => (
              <li key={s.id}>
                <strong>{s.label}</strong>: tried {plural(s.tries, "time", "times")},{" "}
                {s.mistakes === 0 ? "no mistakes" : `${plural(s.mistakes, "mistake", "mistakes")}`},{" "}
                {Math.round(s.mean_reward * 100)}% success score
              </li>
            ))}
          </ul>
          {confirming ? (
            <p>
              Forget everything learned from {plural(summary.scans, "scan", "scans")}? Scans will go
              back to the standard settings and this cannot be undone.{" "}
              <button
                type="button"
                className="danger"
                disabled={busy}
                onClick={() => void forget()}
              >
                Yes, forget it
              </button>{" "}
              <button type="button" disabled={busy} onClick={() => setConfirming(false)}>
                Keep it
              </button>
            </p>
          ) : (
            <button type="button" className="danger" onClick={() => setConfirming(true)}>
              Forget
            </button>
          )}
        </>
      )}
    </section>
  );
}
