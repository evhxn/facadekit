import { useEffect, useRef, useState } from "react";
import { ApiError, fetchSnapshot } from "../../api/client";
import { userMessage } from "../../api/errors";
import ErrorBanner from "../../components/ErrorBanner";
import styles from "../SetupPage.module.css";

/** Pause between pictures. The server answers from memory, so this sets the frame rate. */
export const REFRESH_MS = 200;
/** Waits after the 1st, 2nd... failed picture in a row; after the last one asking stops. */
export const BACKOFF_MS = [1000, 2000, 4000, 8000];
const BUSY_RETRY_MS = 1000;

interface Props {
  cameraId: string;
  /** The camera is busy (camera check): stop asking for pictures. */
  paused: boolean;
  onFrame: (width: number, height: number) => void;
  /** The server does not know this camera (unplugged, or an id from an earlier session). */
  onGone: () => void;
}

/**
 * Live camera picture. Asks for the next picture only after the last one arrived, waits
 * longer after each failure, and stops asking when the camera is gone or keeps failing,
 * instead of requesting a missing camera once a second for ever.
 */
export default function LiveView({ cameraId, paused, onFrame, onGone }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);
  const [attempt, setAttempt] = useState(0); // "Try again" starts the loop afresh
  const onGoneRef = useRef(onGone);
  onGoneRef.current = onGone;

  useEffect(() => {
    if (paused) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let last: string | null = null;
    let failures = 0;
    const controller = new AbortController();
    setStopped(false);

    const later = (ms: number) => {
      timer = setTimeout(() => void tick(), ms);
    };
    const tick = async () => {
      try {
        const blob = await fetchSnapshot(cameraId, controller.signal);
        if (!live) return;
        failures = 0;
        setProblem(null);
        const next = typeof URL.createObjectURL === "function" ? URL.createObjectURL(blob) : "";
        setUrl(next);
        if (last) URL.revokeObjectURL(last);
        last = next;
        later(REFRESH_MS);
      } catch (e) {
        if (!live) return;
        const error = e instanceof ApiError ? e : null;
        if (error?.status === 404) {
          setStopped(true);
          onGoneRef.current();
          return;
        }
        if (error?.status === 409) {
          later(BUSY_RETRY_MS); // a scan holds the camera; not a failure
          return;
        }
        failures += 1;
        setProblem(
          error ? userMessage(error.code, error.message) : "The camera picture did not arrive.",
        );
        const wait = BACKOFF_MS[failures - 1];
        if (wait === undefined) setStopped(true);
        else later(wait);
      }
    };
    void tick();
    return () => {
      live = false;
      controller.abort();
      if (timer !== null) clearTimeout(timer);
      if (last && typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(last);
    };
  }, [cameraId, paused, attempt]);

  return (
    <>
      {url ? (
        <img
          className={styles.live}
          src={url}
          alt="Live camera view"
          onLoad={(e) => {
            const img = e.currentTarget;
            if (img.naturalWidth > 0) onFrame(img.naturalWidth, img.naturalHeight);
          }}
        />
      ) : (
        <div className={`${styles.live} ${styles.livePlaceholder}`} role="status">
          {problem ? "No camera picture" : "Starting the camera…"}
        </div>
      )}
      {problem ? (
        <ErrorBanner
          message={problem}
          actions={
            stopped ? (
              <button type="button" className="primary" onClick={() => setAttempt((n) => n + 1)}>
                Try again
              </button>
            ) : undefined
          }
        />
      ) : null}
    </>
  );
}
