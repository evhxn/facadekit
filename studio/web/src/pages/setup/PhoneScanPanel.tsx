import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  cancelJob,
  remoteInfo,
  startPhoneScan,
  uploadPhoneVideo,
  type RemoteInfo,
} from "../../api/client";
import { userMessage } from "../../api/errors";
import { waitForJob } from "../../api/events";
import type { Job } from "../../api/types";
import ErrorBanner from "../../components/ErrorBanner";
import JobProgress from "../../components/JobProgress";
import QrCode from "../../components/QrCode";
import { COPY } from "./cameraCopy";
import styles from "../SetupPage.module.css";

/**
 * The phone page needs three routes on the LAN allowlist (docs/08 section 4), which is an owner
 * decision that has not been taken. Until then "Send from your phone instead" stays hidden.
 */
export const PHONE_PAGE_ENABLED = false;

interface Props {
  projectId: string;
  /** The video was analysed and the calibration attached to the project. */
  onDone: (calibrationId: string) => void;
  /** Back to the webcam scan. */
  onUseCamera?: () => void;
  /** Show "Send from your phone instead" (QR code to the phone page). */
  phonePage?: boolean;
}

type ShowPhase = "idle" | "running" | "done" | "failed";

/** "Start **light show**" -> text with the bold words as <strong>. */
function Rich({ text }: { text: string }) {
  return (
    <>{text.split("**").map((part, i) => (i % 2 === 1 ? <strong key={i}>{part}</strong> : part))}</>
  );
}

const sessionKey = (pid: string) => `fk_phone_session_${pid}`;

function readSession(pid: string): string | null {
  try {
    return sessionStorage.getItem(sessionKey(pid));
  } catch {
    return null;
  }
}

function writeSession(pid: string, sid: string): void {
  try {
    sessionStorage.setItem(sessionKey(pid), sid);
  } catch {
    // private mode: the session lives in component state only
  }
}

export function phoneErrorMessage(code: string, message: string): string {
  const text = userMessage(code, message);
  return code === "SCAN_FAILED" ? text + COPY.phoneMovedHint : text;
}

/** Text for the light show from its job: the countdown first, then running, then done. */
export function showText(phase: ShowPhase, job: Job | null): string {
  if (phase === "done") return COPY.phoneShowDone;
  if (!job || job.state === "queued") return COPY.phoneShowStarting;
  if (job.message.startsWith("Starting in")) return job.message;
  return COPY.phoneShowRunning;
}

function SendFromPhone({ projectId }: { projectId: string }) {
  const [info, setInfo] = useState<RemoteInfo | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    remoteInfo()
      .then((i) => live && setInfo(i))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, []);
  if (failed) return <p>Could not read the remote settings.</p>;
  if (!info) return <p className={styles.note}>Loading…</p>;
  if (!info.enabled) return <p>{COPY.phoneLanOff}</p>;
  return (
    <>
      <p>{COPY.phonePageIntro}</p>
      {info.urls.map((base) => {
        const url = `${base}/phone/${encodeURIComponent(projectId)}`;
        return (
          <div key={base}>
            <QrCode text={url} className={styles.qr} />
            <code>{url}</code>
          </div>
        );
      })}
      <p>
        Pairing code: <strong>{info.code}</strong>
      </p>
    </>
  );
}

export default function PhoneScanPanel({
  projectId,
  onDone,
  onUseCamera,
  phonePage = PHONE_PAGE_ENABLED,
}: Props) {
  const [sessionId, setSessionId] = useState<string | null>(() => readSession(projectId));
  const [showPhase, setShowPhase] = useState<ShowPhase>("idle");
  const [showJob, setShowJob] = useState<Job | null>(null);
  const [sendFraction, setSendFraction] = useState<number | null>(null);
  const [analysis, setAnalysis] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needSession, setNeedSession] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const startShow = async () => {
    setError(null);
    setNeedSession(false);
    setShowJob(null);
    setShowPhase("running");
    try {
      const { session_id, job_id } = await startPhoneScan(projectId);
      writeSession(projectId, session_id);
      if (!mounted.current) return;
      setSessionId(session_id);
      const final = await waitForJob(job_id, (j) => mounted.current && setShowJob(j));
      if (!mounted.current) return;
      setShowJob(final);
      if (final.state === "succeeded") {
        setShowPhase("done");
      } else {
        setShowPhase("failed");
        setError(
          final.state === "cancelled"
            ? COPY.phoneShowFailed
            : final.error
              ? userMessage(final.error.code, final.error.message)
              : COPY.phoneShowFailed,
        );
      }
    } catch (e) {
      if (!mounted.current) return;
      setShowPhase("failed");
      setError(e instanceof ApiError ? userMessage(e.code, e.message) : COPY.phoneShowFailed);
    }
  };

  const send = async (file: File) => {
    if (!sessionId) {
      setNeedSession(true);
      return;
    }
    setError(null);
    setBusy(true);
    setAnalysis(null);
    setSendFraction(0);
    try {
      const { job_id } = await uploadPhoneVideo(projectId, sessionId, file, (f) => {
        if (mounted.current) setSendFraction(f);
      });
      if (!mounted.current) return;
      setSendFraction(null);
      const final = await waitForJob(job_id, (j) => mounted.current && setAnalysis(j));
      if (!mounted.current) return;
      setAnalysis(final);
      const calId = final.result?.calibration_id;
      if (final.state === "succeeded" && typeof calId === "string") {
        onDone(calId);
        return;
      }
      if (final.state === "cancelled") setError(null);
      else if (final.error) setError(phoneErrorMessage(final.error.code, final.error.message));
      else setError(phoneErrorMessage("SCAN_FAILED", "The scan did not work."));
    } catch (e) {
      if (!mounted.current) return;
      if (e instanceof ApiError && e.status === 404) {
        setSessionId(null); // expired or used up: the user has to play the light show again
      }
      setError(e instanceof ApiError ? phoneErrorMessage(e.code, e.message) : COPY.phoneShowFailed);
    } finally {
      if (mounted.current) {
        setBusy(false);
        setSendFraction(null);
        if (input.current) input.current.value = "";
      }
    }
  };

  const showRunning = showPhase === "running";
  const percent = sendFraction === null ? null : Math.round(sendFraction * 100);

  return (
    <section aria-label={COPY.phoneTitle}>
      <h2>{COPY.phoneTitle}</h2>
      <ol className={styles.steps2}>
        {COPY.phoneSteps.map((step) => (
          <li key={step}>
            <Rich text={step} />
          </li>
        ))}
      </ol>
      <div className={styles.row}>
        <button
          type="button"
          className="primary"
          disabled={showRunning || busy}
          onClick={() => void startShow()}
        >
          {COPY.phoneStart}
        </button>
        <label className={styles.fileButton}>
          <span>{COPY.phoneChoose}</span>
          <input
            ref={input}
            type="file"
            accept="video/*"
            disabled={busy || showRunning}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void send(file);
            }}
          />
        </label>
        {onUseCamera ? (
          <button type="button" disabled={busy || showRunning} onClick={onUseCamera}>
            {COPY.phoneUseCamera}
          </button>
        ) : null}
      </div>
      {showPhase === "running" || showPhase === "done" ? (
        <p role="status" className={styles.countdown}>
          {showText(showPhase, showJob)}
        </p>
      ) : null}
      {showRunning && showJob ? (
        <button type="button" onClick={() => void cancelJob(showJob.id).catch(() => {})}>
          Stop the light show
        </button>
      ) : null}
      {needSession ? <p role="alert">{COPY.phoneNoSession}</p> : null}
      {percent !== null ? (
        <div>
          <p>{COPY.phoneSending}</p>
          <progress max={100} value={percent} aria-label="Upload progress" /> {percent}%
        </div>
      ) : null}
      {busy && percent === null ? (
        analysis ? (
          <JobProgress
            job={analysis}
            onCancel={() => void cancelJob(analysis.id).catch(() => {})}
          />
        ) : (
          <p className={styles.note}>{COPY.phoneChecking}</p>
        )
      ) : null}
      {error ? <ErrorBanner message={error} /> : null}
      <p className={styles.note}>{COPY.phonePrivacy}</p>
      {phonePage ? (
        <details>
          <summary>{COPY.phoneSendFromPhone}</summary>
          <SendFromPhone projectId={projectId} />
        </details>
      ) : null}
    </section>
  );
}
