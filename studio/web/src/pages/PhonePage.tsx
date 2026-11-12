import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, getProject, startPhoneScan, uploadPhoneVideo } from "../api/client";
import { userMessage } from "../api/errors";
import PairForm from "../components/PairForm";
import { COPY } from "./setup/cameraCopy";
import { phoneErrorMessage } from "./setup/PhoneScanPanel";
import styles from "./RemotePage.module.css";

/** The phone's light-show session, kept for this tab only (survives the switch to the camera). */
const STORAGE_KEY = "fk_phone_session";

function readSession(pid: string): string | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { pid?: unknown; sid?: unknown };
    return parsed.pid === pid && typeof parsed.sid === "string" ? parsed.sid : null;
  } catch {
    return null;
  }
}

function writeSession(pid: string, sid: string | null): void {
  try {
    if (sid) sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ pid, sid }));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // private mode: keep it in memory only
  }
}

type Status = "loading" | "pair" | "ready" | "error";

/** /phone/:projectId: start the light show and send the recording from the phone. */
export default function PhonePage() {
  const { projectId = "" } = useParams();
  const [status, setStatus] = useState<Status>("loading");
  const [sessionId, setSessionId] = useState<string | null>(() => readSession(projectId));
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [fraction, setFraction] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const uploading = useRef<AbortController | null>(null);
  useEffect(() => () => uploading.current?.abort(), []);

  const tryLoad = useCallback(async () => {
    try {
      await getProject(projectId);
      setStatus("ready");
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) setStatus("pair");
      else {
        setStatus("error");
        setError(
          e instanceof ApiError
            ? userMessage(e.code, e.message)
            : "Could not load the project. Scan the QR code on the computer again.",
        );
      }
    }
  }, [projectId]);

  useEffect(() => {
    void tryLoad();
  }, [tryLoad]);

  const failed = (e: unknown) => {
    if (e instanceof ApiError && e.status === 403) setError(COPY.phonePageBlocked);
    else if (e instanceof ApiError) setError(phoneErrorMessage(e.code, e.message));
    else setError("Something went wrong. Try again.");
  };

  const start = async () => {
    setError(null);
    setMessage(null);
    setStarting(true);
    try {
      const { session_id } = await startPhoneScan(projectId, 20);
      writeSession(projectId, session_id);
      setSessionId(session_id);
      setMessage(COPY.phonePageSwitch);
    } catch (e) {
      failed(e);
    } finally {
      setStarting(false);
    }
  };

  const send = async (file: File) => {
    if (!sessionId) return;
    setError(null);
    setMessage(null);
    setFraction(0);
    try {
      const controller = new AbortController();
      uploading.current = controller;
      await uploadPhoneVideo(projectId, sessionId, file, setFraction, controller.signal);
      uploading.current = null;
      writeSession(projectId, null);
      setSessionId(null);
      setMessage(COPY.phonePageDone);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        writeSession(projectId, null);
        setSessionId(null);
      }
      failed(e);
    } finally {
      setFraction(null);
      if (input.current) input.current.value = "";
    }
  };

  if (status === "pair") return <PairForm onPaired={tryLoad} />;
  if (status === "loading") return <p className={styles.page}>Loading…</p>;
  if (status === "error") return <p className={styles.page}>{error}</p>;

  const percent = fraction === null ? null : Math.round(fraction * 100);
  return (
    <div className={styles.page}>
      <h1>{COPY.phonePageTitle}</h1>
      <button
        type="button"
        className={styles.big}
        disabled={starting || percent !== null}
        onClick={() => void start()}
      >
        {COPY.phonePageStart}
      </button>
      <label className={styles.big} aria-disabled={!sessionId || percent !== null}>
        {COPY.phonePageSend}
        <input
          ref={input}
          type="file"
          accept="video/*"
          disabled={!sessionId || percent !== null}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void send(file);
          }}
        />
      </label>
      {!sessionId && !message ? <p>{COPY.phoneNoSession}</p> : null}
      {percent !== null ? (
        <p>
          <progress max={100} value={percent} aria-label="Upload progress" /> {percent}%
        </p>
      ) : null}
      {message ? <p role="status">{message}</p> : null}
      {error ? (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      ) : null}
      <p className={styles.note}>{COPY.phonePrivacy}</p>
    </div>
  );
}
