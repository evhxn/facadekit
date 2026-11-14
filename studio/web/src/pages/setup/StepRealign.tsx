import { useEffect, useRef, useState } from "react";
import { ApiError, cancelJob, getCalibration, projViewUrl, realign, startCalibration } from "../../api/client";
import type { RealignResponse } from "../../api/client";
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
  /** Go to the camera step (shown when a scan is asked for without a camera). */
  onChooseCamera?: () => void;
}

type Phase = "idle" | "scanning" | "preview" | "failed" | "applied" | "kept";

const polygon = (pts: readonly (readonly [number, number])[]) =>
  pts.map(([x, y]) => `${x},${y}`).join(" ");

export default function StepRealign({ project, cameraId, onChooseCamera }: Props) {
  const pid = project.id;
  const [phase, setPhase] = useState<Phase>("idle");
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ids, setIds] = useState<{ old: string; next: string } | null>(null);
  const [preview, setPreview] = useState<RealignResponse | null>(null);
  const [oldSurfaces, setOldSurfaces] = useState(project.surfaces);
  const [busy, setBusy] = useState(false);
  const [needsCamera, setNeedsCamera] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const start = async () => {
    const oldId = project.calibration_id;
    if (!oldId) return;
    if (!cameraId) {
      // The request used to go out without a camera and come back as an unexplained 422.
      setError("No camera is chosen yet. Choose the camera that will watch the projection, then scan again.");
      setNeedsCamera(true);
      setPhase("failed");
      return;
    }
    setNeedsCamera(false);
    setError(null);
    setPreview(null);
    setOldSurfaces(project.surfaces);
    setPhase("scanning");
    setJob(null);
    try {
      // Scan with the same crop as the scan being compared against, so both see the same picture.
      let earlier: Calibration | null = null;
      try {
        earlier = await getCalibration(pid, oldId);
      } catch {
        // Without the old settings the scan still works; the maps are stored for the whole picture.
      }
      const sameCamera = !cameraId || !earlier?.camera_id || earlier.camera_id === cameraId;
      const roi = sameCamera ? earlier?.settings?.roi : null;
      const body: Parameters<typeof startCalibration>[1] = { mode: "scan", camera_id: cameraId };
      if (roi) body.settings = { roi };
      const { job_id } = await startCalibration(pid, body);
      const final = await waitForJob(job_id, (j) => mounted.current && setJob(j));
      if (!mounted.current) return;
      if (final.state !== "succeeded") {
        setError(
          final.error
            ? userMessage(final.error.code, final.error.message)
            : "The scan did not work. Try again, or keep the old scan and place surfaces by hand.",
        );
        setPhase("failed");
        return;
      }
      const newId = final.result?.calibration_id;
      if (typeof newId !== "string") throw new Error("missing calibration");
      setIds({ old: oldId, next: newId });
      try {
        const res = await realign(pid, {
          old_calibration_id: oldId,
          new_calibration_id: newId,
          apply: false,
        });
        if (!mounted.current) return;
        setPreview(res);
        setPhase("preview");
      } catch (e) {
        if (!mounted.current) return;
        setError(e instanceof ApiError ? userMessage(e.code, e.message) : "Re-align failed. Keep the new scan and place the surfaces by hand.");
        setPhase("failed");
        await useProjectStore.getState().load(pid);
      }
    } catch (e) {
      if (!mounted.current) return;
      setError(e instanceof ApiError ? userMessage(e.code, e.message) : "The scan failed. Check that the camera and projector window are open, then try again.");
      setPhase("failed");
    }
  };

  const apply = async () => {
    if (!ids) return;
    setBusy(true);
    try {
      await realign(pid, {
        old_calibration_id: ids.old,
        new_calibration_id: ids.next,
        apply: true,
      });
      await useProjectStore.getState().load(pid);
      if (mounted.current) setPhase("applied");
    } catch (e) {
      if (mounted.current) {
        setError(e instanceof ApiError ? userMessage(e.code, e.message) : "Could not apply the change. Try Apply again, or place the surfaces by hand.");
        setPhase("failed");
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const keep = async () => {
    await useProjectStore.getState().load(pid);
    if (mounted.current) setPhase("kept");
  };

  if (phase === "idle") {
    return (
      <section>
        <button type="button" onClick={() => void start()}>
          Projector moved? Re-scan and re-align
        </button>
      </section>
    );
  }
  if (phase === "scanning") {
    return (
      <section>
        <p>Do not walk in front of the projector.</p>
        {job ? (
          <JobProgress job={job} onCancel={() => void cancelJob(job.id).catch(() => {})} />
        ) : (
          <p className={styles.note}>Starting…</p>
        )}
      </section>
    );
  }
  if (phase === "applied") {
    return (
      <section>
        <p>Surfaces moved. Check the outlines on the house and re-render the show.</p>
      </section>
    );
  }
  if (phase === "kept") {
    return (
      <section>
        <p>New scan kept. Place the surfaces by hand on the Surfaces page.</p>
      </section>
    );
  }
  const canKeep = ids !== null;
  return (
    <section>
      {phase === "failed" && error ? (
        <ErrorBanner
          message={error}
          actions={
            needsCamera && onChooseCamera ? (
              <button type="button" className="primary" onClick={onChooseCamera}>
                Choose a camera
              </button>
            ) : undefined
          }
        />
      ) : null}
      {phase === "preview" && preview && ids ? (
        <>
          <div className={styles.viewWrap}>
            <img
              className={styles.view}
              src={projViewUrl(pid, ids.next)}
              alt="New projector view of the house"
            />
            <svg
              className={styles.overlay}
              viewBox={`0 0 ${project.projector.width} ${project.projector.height}`}
              aria-hidden="true"
            >
              {oldSurfaces.map((s) => (
                <polygon
                  key={`old-${s.id}`}
                  points={polygon(s.quad)}
                  fill="none"
                  stroke="red"
                  strokeWidth={3}
                  strokeDasharray="10 6"
                />
              ))}
              {(preview.project?.surfaces ?? []).map((s) => (
                <polygon
                  key={`new-${s.id}`}
                  points={polygon(s.quad)}
                  fill="none"
                  stroke="lime"
                  strokeWidth={3}
                />
              ))}
            </svg>
          </div>
          <p>Surfaces moved by up to {Math.round(preview.max_shift_px)} px.</p>
          {preview.warning ? (
            <div className={styles.warning} role="status">
              <p>{preview.warning}</p>
              <p>
                Compare the red dashed outlines (old) with the green ones (new). If the green
                outlines do not sit on the house, choose &quot;Keep new scan but place surfaces by
                hand&quot;. If they look right, press Apply.
              </p>
            </div>
          ) : null}
          <div className={styles.row}>
            <button type="button" disabled={busy} onClick={() => void apply()} className="primary">
              Apply
            </button>
            <button type="button" disabled={busy} onClick={() => void keep()}>
              Keep new scan but place surfaces by hand
            </button>
          </div>
        </>
      ) : (
        <div className={styles.row}>
          {canKeep ? (
            <button type="button" onClick={() => void keep()}>
              Keep new scan but place surfaces by hand
            </button>
          ) : (
            <button type="button" onClick={() => setPhase("idle")}>
              Back
            </button>
          )}
        </div>
      )}
    </section>
  );
}
