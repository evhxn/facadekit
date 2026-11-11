import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  cancelJob,
  getCalibration,
  measuredMaskUrl,
  projViewUrl,
  sendScanFeedback,
  startCalibration,
} from "../../api/client";
import { userMessage } from "../../api/errors";
import { waitForJob } from "../../api/events";
import type { CalibSettings, Calibration, CameraRoi, Job, Project } from "../../api/types";
import ErrorBanner from "../../components/ErrorBanner";
import JobProgress from "../../components/JobProgress";
import { COPY, isPictureSizeMessage } from "./cameraCopy";
import LearningPanel from "./LearningPanel";
import { useProjectStore } from "../../state/projectStore";
import styles from "../SetupPage.module.css";

interface Props {
  project: Project;
  cameraId: string | null;
  /** FPP address when the projector is plugged into the Raspberry Pi; null = this computer. */
  piHost?: string | null;
  /** Crop of the camera picture chosen on the camera step; sent with camera scans only. */
  roi?: CameraRoi | null;
  /** Forget the crop (it was drawn for another picture size). */
  onRoiReset?: () => void;
  /** The user chose "Use my phone instead". */
  phone?: boolean;
}

/** Projector-view photo; where the measured mask is black, a red tint is laid over it. */
function TintedView({ photoUrl, maskUrl }: { photoUrl: string; maskUrl?: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!maskUrl) return;
    const img = new Image();
    img.onload = () => {
      const el = canvas.current;
      const ctx = el?.getContext("2d");
      if (!el || !ctx) return;
      el.width = img.naturalWidth;
      el.height = img.naturalHeight;
      ctx.drawImage(img, 0, 0);
      const data = ctx.getImageData(0, 0, el.width, el.height);
      const px = data.data;
      for (let i = 0; i < px.length; i += 4) {
        const lum = ((px[i] ?? 0) + (px[i + 1] ?? 0) + (px[i + 2] ?? 0)) / 3;
        const unmeasured = lum < 128;
        px[i] = 255;
        px[i + 1] = 0;
        px[i + 2] = 0;
        px[i + 3] = unmeasured ? Math.round(0.35 * 255) : 0;
      }
      ctx.putImageData(data, 0, 0);
    };
    img.src = maskUrl;
  }, [maskUrl]);

  return (
    <div className={styles.viewWrap}>
      <img className={styles.view} src={photoUrl} alt="Projector view of the house" />
      {maskUrl ? <canvas ref={canvas} className={styles.overlay} aria-hidden="true" /> : null}
    </div>
  );
}

/** Plain-language names for the strategies scan memory can pick (backend calib/learn.py). */
const STRATEGY_NOTES: Record<string, string> = {
  low_light: "extra exposures for a faint picture",
  soft_focus: "wider stripes for a soft or out-of-focus picture",
  low_light_soft: "wider stripes and extra exposures",
};

type Phase = "idle" | "running" | "failed" | "done";

export default function StepScan({
  project,
  cameraId,
  piHost = null,
  roi = null,
  onRoiReset,
  phone = false,
}: Props) {
  const navigate = useNavigate();
  const pid = project.id;
  const [phase, setPhase] = useState<Phase>(project.calibration_id ? "done" : "idle");
  const [job, setJob] = useState<Job | null>(null);
  const [calibration, setCalibration] = useState<Calibration | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fixedWait, setFixedWait] = useState(false);
  const [waitMs, setWaitMs] = useState(300);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!project.calibration_id || calibration) return;
    getCalibration(pid, project.calibration_id)
      .then((c) => mounted.current && setCalibration(c))
      .catch(() => mounted.current && setPhase("idle"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, project.calibration_id]);

  const fail = (message: string) => {
    if (isPictureSizeMessage(message)) onRoiReset?.();
    setError(message);
  };

  const run = async (mode: "scan" | "manual") => {
    setError(null);
    setPhase("running");
    setJob(null);
    try {
      // settle_ms 0 means "automatic": the backend measures the projector-to-camera delay.
      // The backend fills in the other fields, so only this one is sent.
      const settle_ms = fixedWait ? Math.min(1500, Math.max(300, Math.round(waitMs) || 300)) : 0;
      const settings: Partial<CalibSettings> = { settle_ms };
      if (roi) settings.roi = roi; // only used by the camera branch below; Pi scans ignore it
      let body: Parameters<typeof startCalibration>[1] = { mode: "manual" };
      if (mode === "scan" && cameraId) {
        body = piHost
          ? { mode: "scan_pi", camera_id: cameraId, host: piHost }
          : { mode, camera_id: cameraId, settings };
      }
      const { job_id } = await startCalibration(pid, body);
      const final = await waitForJob(job_id, (j) => mounted.current && setJob(j));
      if (!mounted.current) return;
      setJob(final);
      if (final.state !== "succeeded") {
        setPhase("failed");
        if (final.state === "cancelled") setError("The scan was cancelled. Press Scan again when you are ready.");
        else if (final.error) fail(userMessage(final.error.code, final.error.message));
        else setError(userMessage("SCAN_FAILED", "The scan did not work."));
        return;
      }
      await useProjectStore.getState().load(pid);
      const calId =
        typeof final.result?.calibration_id === "string"
          ? final.result.calibration_id
          : useProjectStore.getState().project?.calibration_id;
      if (!calId) throw new Error("missing calibration");
      const cal = await getCalibration(pid, calId);
      if (!mounted.current) return;
      setCalibration(cal);
      setPhase("done");
    } catch (e) {
      if (!mounted.current) return;
      fail(
        e instanceof ApiError
          ? userMessage(e.code, e.message)
          : "The scan result could not be loaded. Press Scan again.",
      );
      setPhase("failed");
    }
  };

  const cancel = () => {
    if (job) void cancelJob(job.id).catch(() => {});
  };

  if (phase === "done" && calibration) {
    const warnings = calibration.warnings;
    const low = warnings.includes("LOW_COVERAGE");
    const coverage = calibration.quality ? Math.round(calibration.quality.coverage * 100) : null;
    const strategyNote = calibration.strategy ? STRATEGY_NOTES[calibration.strategy] : undefined;
    // Feedback is best effort: the wizard works the same when it cannot be stored.
    const feedback = (verdict: "good" | "bad") =>
      void sendScanFeedback(pid, calibration.id, verdict).catch(() => {});
    return (
      <section>
        <TintedView
          photoUrl={projViewUrl(pid, calibration.id)}
          maskUrl={low ? measuredMaskUrl(pid, calibration.id) : undefined}
        />
        {coverage !== null ? <p>Measured {coverage}% of the projection.</p> : null}
        {strategyNote ? (
          <p className={styles.note}>
            Earlier scans with this camera had trouble, so this one used {strategyNote}.
          </p>
        ) : null}
        {low ? (
          <p>The camera could not see the red areas. Surfaces there may need manual alignment.</p>
        ) : null}
        {calibration.quality?.verdict === "marginal" ||
        calibration.quality?.verdict === "too_coarse" ? (
          <p>{COPY.smallResult}</p>
        ) : null}
        {warnings.includes("CROP_TOO_TIGHT") ? <p>{COPY.cropTight}</p> : null}
        {warnings.includes("NON_PLANAR") ? (
          <p>This wall is not flat. That is OK, but check alignment carefully.</p>
        ) : null}
        {warnings.includes("CAMERA_CONTROLS_UNAVAILABLE") ? (
          <p className={styles.note}>
            This camera does not allow exposure control. That is usually fine.
          </p>
        ) : null}
        <div className={styles.row}>
          <button
            type="button"
            onClick={() => {
              feedback("good");
              navigate(`/p/${pid}/surfaces`);
            }}
            className="primary"
          >
            Continue to surfaces
          </button>
          <button type="button" onClick={() => void run("scan")}>
            Scan again
          </button>
          <button
            type="button"
            onClick={() => {
              feedback("bad");
              void run("scan");
            }}
          >
            Picture looks wrong, try another way
          </button>
        </div>
        <p className={styles.note}>
          FacadeKit remembers which scans worked on this computer and adapts the next one.
        </p>
      </section>
    );
  }

  return (
    <section>
      {phase === "running" ? (
        <>
          <p>Do not walk in front of the projector.</p>
          {job ? (
            <JobProgress job={job} onCancel={cancel} />
          ) : (
            <p className={styles.note}>Starting…</p>
          )}
        </>
      ) : (
        <>
          {phone ? <p className={styles.note}>{COPY.phoneSoon}</p> : null}
          {piHost ? (
            <p>The scan takes about a minute. FPP will stop whatever it is playing.</p>
          ) : (
            <p>
              The projector will show stripes while the camera watches. This takes a few minutes.
            </p>
          )}
          <details>
            <summary>Advanced</summary>
            <div hidden={piHost !== null}>
              <p className={styles.note}>
                By default the scan measures how long the camera takes to see the projector and
                chooses exposure automatically.
              </p>
              <label>
                <input
                  type="checkbox"
                  checked={fixedWait}
                  onChange={(e) => setFixedWait(e.target.checked)}
                />{" "}
                Use a fixed wait instead
              </label>
              {fixedWait ? (
                <label>
                  {" "}
                  <input
                    type="number"
                    min={300}
                    max={1500}
                    step={50}
                    value={waitMs}
                    onChange={(e) => setWaitMs(Number(e.target.value))}
                    aria-label="Wait after each image (ms)"
                  />{" "}
                  ms
                </label>
              ) : null}
            </div>
            <LearningPanel />
          </details>
          {phase === "failed" && error ? (
            <ErrorBanner
              message={error}
              actions={
                <>
                  <button type="button" onClick={() => void run("scan")} className="primary">
                    Retry
                  </button>
                  <button type="button" onClick={() => void run("manual")}>
                    Use manual mode
                  </button>
                </>
              }
            />
          ) : (
            <div className={styles.row}>
              <button type="button" onClick={() => void run("scan")} className="primary">
                Start scan
              </button>
              <button type="button" onClick={() => void run("manual")}>
                Skip scan (manual mode)
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
