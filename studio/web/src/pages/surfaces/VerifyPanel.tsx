import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  cancelJob,
  getVerification,
  startVerify,
  verificationOverlayUrl,
} from "../../api/client";
import { userMessage } from "../../api/errors";
import { waitForJob } from "../../api/events";
import type { Job, Mask, Pt, Surface, VerificationRecord } from "../../api/types";
import ErrorBanner from "../../components/ErrorBanner";
import JobProgress from "../../components/JobProgress";
import type { Quad } from "./geometry";
import styles from "./VerifyPanel.module.css";

export interface VerifyPanelProps {
  projectId: string;
  surface: Surface;
  /** Set when verification cannot run (manual calibration). */
  disabledReason?: string;
  /** Start a check as soon as the panel shows this surface (after Magic select). */
  autoStart?: boolean;
  onAutoStarted?: () => void;
  onApply: (quad: Quad, mask: Mask | null) => void;
  /** True while a check holds the projector; the page pauses its outline display. */
  onBusy: (busy: boolean) => void;
  /** Runs before a check starts (the page saves the outline, which the server reads). */
  prepare?: () => Promise<void>;
}

const VERDICT_TEXT = { pass: "On target", warn: "Check it", fail: "Off target" } as const;

function samePoints(a: Pt[] | null | undefined, b: Pt[] | null | undefined): boolean {
  if (!a || !b) return !a && !b;
  if (a.length !== b.length) return false;
  return a.every((p, i) => {
    const q = b[i] as Pt;
    return Math.abs(p[0] - q[0]) < 0.01 && Math.abs(p[1] - q[1]) < 0.01;
  });
}

/** Has the surface's outline changed since this check? */
export function isOutdated(record: VerificationRecord, surface: Surface): boolean {
  return (
    !samePoints(record.surface_quad, surface.quad) ||
    !samePoints(record.surface_mask?.outer, surface.mask?.outer)
  );
}

function pct(v: number | null): string {
  return v === null ? "–" : `${(v * 100).toFixed(1)} %`;
}

function px(v: number | null): string {
  return v === null ? "–" : `${v.toFixed(1)} px`;
}

export default function VerifyPanel({
  projectId,
  surface,
  disabledReason,
  autoStart,
  onAutoStarted,
  onApply,
  onBusy,
  prepare,
}: VerifyPanelProps) {
  const [record, setRecord] = useState<VerificationRecord | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [applied, setApplied] = useState(false);
  const alive = useRef(true);
  const busyRef = useRef(onBusy);
  busyRef.current = onBusy;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // The last saved check of this surface, if any.
  useEffect(() => {
    let cancelled = false;
    setRecord(null);
    setApplied(false);
    getVerification(projectId, surface.id)
      .then((r) => {
        if (!cancelled) setRecord(r);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [projectId, surface.id]);

  const run = useCallback(async () => {
    setError("");
    setApplied(false);
    let started: { job_id: string };
    try {
      if (prepare) await prepare();
      started = await startVerify(projectId, surface.id);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? userMessage(e.code, e.message)
          : "The check could not be started. Try again.",
      );
      return;
    }
    busyRef.current(true);
    setJob({
      id: started.job_id,
      kind: "calibration",
      state: "queued",
      progress: 0,
      message: "",
      error: null,
      result: null,
      created_at: "",
    });
    try {
      const done = await waitForJob(started.job_id, (j) => {
        if (alive.current) setJob(j);
      });
      if (!alive.current) return;
      if (done.state === "succeeded") {
        const rec = (done.result as { verification?: VerificationRecord } | null)?.verification;
        if (rec) setRecord(rec);
      } else if (done.state === "failed" && done.error) {
        setError(userMessage(done.error.code, done.error.message));
      }
    } finally {
      busyRef.current(false);
      if (alive.current) setJob(null);
    }
  }, [projectId, surface.id, prepare]);

  const autoRan = useRef(false);
  useEffect(() => {
    if (!autoStart || autoRan.current || disabledReason) return;
    autoRan.current = true;
    onAutoStarted?.();
    void run();
  }, [autoStart, disabledReason, onAutoStarted, run]);

  const s = record?.surface ?? null;
  const outdated = record !== null && isOutdated(record, surface);
  const running = job !== null;
  return (
    <section className={styles.verify} aria-label="Check on the house">
      <h2>Check on the house</h2>
      <p className={styles.help}>
        Lights this surface and checks with the camera that it lands on the object, and that the
        camera and projector have not moved since the scan. Takes a few seconds; the projector
        shows test patterns meanwhile.
      </p>
      <button
        type="button"
        className="primary"
        onClick={() => void run()}
        disabled={running || !!disabledReason}
        title={disabledReason}
      >
        {record ? "Check again" : "Check on the house"}
      </button>
      {disabledReason && <small>{disabledReason}</small>}
      {job && (
        <JobProgress
          job={job}
          onCancel={() => {
            void cancelJob(job.id).catch(() => {});
          }}
        />
      )}
      {error && <ErrorBanner message={error} />}
      {record && s && !running && (
        <div className={styles.result} data-verdict={s.verdict} role="status">
          <p className={styles.headline}>
            <span className={styles.badge} data-verdict={s.verdict}>
              {VERDICT_TEXT[s.verdict]}
            </span>{" "}
            {s.message}
          </p>
          {outdated && (
            <p className={styles.outdated}>
              The outline changed since this check. Check again to see the new numbers.
            </p>
          )}
          {record.rig.verdict !== "stable" && (
            <p className={styles.rig} data-rig={record.rig.verdict}>
              {record.rig.message} {record.rig.recommendation}
            </p>
          )}
          {record.overlay && (
            <figure className={styles.figure}>
              <img
                src={verificationOverlayUrl(projectId, surface.id, record.id)}
                alt="Camera view of the surface: green is light on the object, red is light on the background, blue is the object left dark"
              />
              <figcaption>
                Green: light on the object. Red: light on the background. Blue: object left dark.
                White line: where the light should be. Yellow line: the object.
              </figcaption>
            </figure>
          )}
          <ul className={styles.edges}>
            {s.edges.map((e) => (
              <li key={e.side}>{e.advice}</li>
            ))}
          </ul>
          <dl className={styles.numbers}>
            <dt>Light off the object</dt>
            <dd>{pct(s.leakage)}</dd>
            <dt>Object left dark</dt>
            <dd>{pct(s.uncovered)}</dd>
            <dt>Worst edge</dt>
            <dd>{px(s.max_edge_px)}</dd>
            <dt>Camera and projector</dt>
            <dd>{record.rig.verdict === "stable" ? "not moved" : record.rig.verdict.replace("_", " ")}</dd>
          </dl>
          {s.suggested_quad && !applied && !outdated && (
            <button
              type="button"
              onClick={() => {
                onApply(s.suggested_quad as Quad, s.suggested_mask);
                setApplied(true);
              }}
            >
              Apply suggested fix
            </button>
          )}
          {applied && <p>Applied. Check again to confirm it on the house.</p>}
          {!s.suggested_quad && s.suggestion_note && s.verdict !== "pass" && (
            <small>{s.suggestion_note}</small>
          )}
        </div>
      )}
    </section>
  );
}
