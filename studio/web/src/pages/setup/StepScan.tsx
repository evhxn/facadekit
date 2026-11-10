import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  cancelJob,
  getCalibration,
  measuredMaskUrl,
  projViewUrl,
  startCalibration,
} from "../../api/client";
import { userMessage } from "../../api/errors";
import { waitForJob } from "../../api/events";
import type { Calibration, Job, Project } from "../../api/types";
import ErrorBanner from "../../components/ErrorBanner";
import JobProgress from "../../components/JobProgress";
import { useProjectStore } from "../../state/projectStore";
import styles from "../SetupPage.module.css";

interface Props {
  project: Project;
  cameraId: string | null;
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

type Phase = "idle" | "running" | "failed" | "done";

export default function StepScan({ project, cameraId }: Props) {
  const navigate = useNavigate();
  const pid = project.id;
  const [phase, setPhase] = useState<Phase>(project.calibration_id ? "done" : "idle");
  const [job, setJob] = useState<Job | null>(null);
  const [calibration, setCalibration] = useState<Calibration | null>(null);
  const [error, setError] = useState<string | null>(null);
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

  const run = async (mode: "scan" | "manual") => {
    setError(null);
    setPhase("running");
    setJob(null);
    try {
      const body = mode === "scan" && cameraId ? { mode, camera_id: cameraId } : { mode };
      const { job_id } = await startCalibration(pid, body);
      const final = await waitForJob(job_id, (j) => mounted.current && setJob(j));
      if (!mounted.current) return;
      setJob(final);
      if (final.state !== "succeeded") {
        setPhase("failed");
        if (final.state === "cancelled") setError("The scan was cancelled.");
        else if (final.error) setError(userMessage(final.error.code, final.error.message));
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
      setError(
        e instanceof ApiError
          ? userMessage(e.code, e.message)
          : "The scan result could not be loaded.",
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
    return (
      <section>
        <TintedView
          photoUrl={projViewUrl(pid, calibration.id)}
          maskUrl={low ? measuredMaskUrl(pid, calibration.id) : undefined}
        />
        {coverage !== null ? <p>Measured {coverage}% of the projection.</p> : null}
        {low ? (
          <p>The camera could not see the red areas. Surfaces there may need manual alignment.</p>
        ) : null}
        {warnings.includes("NON_PLANAR") ? (
          <p>This wall is not flat. That is OK, but check alignment carefully.</p>
        ) : null}
        {warnings.includes("CAMERA_CONTROLS_UNAVAILABLE") ? (
          <p className={styles.note}>
            This camera does not allow exposure control. That is usually fine.
          </p>
        ) : null}
        <div className={styles.row}>
          <button type="button" onClick={() => navigate(`/p/${pid}/surfaces`)}>
            Continue to surfaces
          </button>
          <button type="button" onClick={() => void run("scan")}>
            Scan again
          </button>
        </div>
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
          <p>The projector will show stripes while the camera watches. This takes a few minutes.</p>
          {phase === "failed" && error ? (
            <ErrorBanner
              message={error}
              actions={
                <>
                  <button type="button" onClick={() => void run("scan")}>
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
              <button type="button" onClick={() => void run("scan")}>
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
