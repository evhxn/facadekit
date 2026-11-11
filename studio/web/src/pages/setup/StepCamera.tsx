import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  cameraSnapshotUrl,
  listCameras,
  projectorShow,
  setCameraControls,
  startCameraCheck,
} from "../../api/client";
import { userMessage } from "../../api/errors";
import { waitForJob } from "../../api/events";
import type { CameraCheck, CameraInfo, CameraRoi } from "../../api/types";
import ErrorBanner from "../../components/ErrorBanner";
import styles from "../SetupPage.module.css";
import CameraCheckCard from "./CameraCheckCard";
import { COPY, isPictureSizeMessage } from "./cameraCopy";
import CropBox from "./CropBox";

const REFRESH_MS = 350;
const DEBOUNCE_MS = 200;

interface Props {
  projectId: string;
  cameraId: string | null;
  onCameraChange: (id: string | null) => void;
  /** Crop shared with the scan step; null = the whole picture. */
  roi: CameraRoi | null;
  onRoiChange: (roi: CameraRoi | null) => void;
  /** True while the crop was taken over from the last scan. */
  roiFromLastScan: boolean;
  check: CameraCheck | null;
  onCheckChange: (check: CameraCheck | null) => void;
  /** Go on to the scan step. */
  onNext: () => void;
  onUsePhone: () => void;
}

interface LiveProps {
  cameraId: string;
  /** The camera is busy (camera check): stop asking for pictures. */
  paused: boolean;
  onFrame: (width: number, height: number) => void;
}

function LiveView({ cameraId, paused, onFrame }: LiveProps) {
  const [src, setSrc] = useState(() => `${cameraSnapshotUrl(cameraId)}?t=${Date.now()}`);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  useEffect(() => {
    setSrc(`${cameraSnapshotUrl(cameraId)}?t=${Date.now()}`);
    return () => {
      if (timer.current !== null) clearTimeout(timer.current);
    };
  }, [cameraId]);

  // Start again when the camera is free.
  useEffect(() => {
    if (!paused) setSrc(`${cameraSnapshotUrl(cameraId)}?t=${Date.now()}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paused]);

  // Next request starts only after the previous image finished (or failed) loading.
  const scheduleNext = () => {
    if (timer.current !== null) clearTimeout(timer.current);
    if (pausedRef.current) return;
    timer.current = setTimeout(() => {
      if (!pausedRef.current) setSrc(`${cameraSnapshotUrl(cameraId)}?t=${Date.now()}`);
    }, REFRESH_MS);
  };

  return (
    <img
      className={styles.live}
      src={src}
      alt="Live camera view"
      onLoad={(e) => {
        const img = e.currentTarget;
        if (img.naturalWidth > 0) onFrame(img.naturalWidth, img.naturalHeight);
        scheduleNext();
      }}
      onError={scheduleNext}
    />
  );
}

/** Zoomed view of the cropped area. Loaded once; the live view keeps the camera busy otherwise. */
function CropPreview({ cameraId, roi }: { cameraId: string; roi: CameraRoi }) {
  const [stamp] = useState(() => Date.now());
  const url = cameraSnapshotUrl(cameraId, roi);
  return (
    <img
      className={styles.cropPreview}
      src={`${url}${url.includes("?") ? "&" : "?"}t=${stamp}`}
      alt={COPY.cropPreviewAlt}
    />
  );
}

export default function StepCamera({
  projectId,
  cameraId,
  onCameraChange,
  roi,
  onRoiChange,
  roiFromLastScan,
  check,
  onCheckChange,
  onNext,
  onUsePhone,
}: Props) {
  const [cameras, setCameras] = useState<CameraInfo[] | null>(null);
  const [mode, setMode] = useState<"grid" | "white">("grid");
  const [exposure, setExposure] = useState(0.5);
  const [exposureUnavailable, setExposureUnavailable] = useState(false);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [frame, setFrame] = useState<{ width: number; height: number } | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // A crop belongs to one camera picture: forget the old size when the camera changes.
  useEffect(() => {
    setFrame(null);
    setEditing(false);
    setCheckError(null);
  }, [cameraId]);

  const shownFrame = useMemo(
    () => frame ?? (check ? { width: check.camera_width, height: check.camera_height } : { width: 1280, height: 720 }),
    [frame, check],
  );
  const editRoi = roi ?? check?.suggested_roi ?? null;
  const wholePicture = useMemo<CameraRoi>(
    () => ({ x: 0, y: 0, w: shownFrame.width, h: shownFrame.height, frame_width: shownFrame.width, frame_height: shownFrame.height, bin: 1 }),
    [shownFrame],
  );

  const runCheck = async () => {
    if (!cameraId || checking) return;
    setCheckError(null);
    setEditing(false);
    setChecking(true);
    try {
      const { job_id } = await startCameraCheck(projectId, cameraId, roi);
      const final = await waitForJob(job_id);
      if (!mounted.current) return;
      const result = final.result?.check as CameraCheck | undefined;
      if (final.state === "succeeded" && result) {
        onCheckChange(result);
      } else {
        onCheckChange(null);
        const message = final.error
          ? userMessage(final.error.code, final.error.message)
          : COPY.checkFailed;
        if (isPictureSizeMessage(message)) onRoiChange(null);
        setCheckError(message);
      }
    } catch (e) {
      if (!mounted.current) return;
      onCheckChange(null);
      const message = e instanceof ApiError ? userMessage(e.code, e.message) : COPY.checkFailed;
      if (isPictureSizeMessage(message)) onRoiChange(null);
      setCheckError(message);
    } finally {
      if (mounted.current) setChecking(false);
      // The check leaves the projector on stripes; put the wizard's picture back.
      void Promise.resolve(projectorShow({ mode })).catch(() => {});
    }
  };

  const refresh = () => {
    listCameras()
      .then((list) => {
        setCameras(list);
        if (list.length === 1 && list[0] && !cameraId) onCameraChange(list[0].id);
        if (cameraId && !list.some((c) => c.id === cameraId)) onCameraChange(null);
      })
      .catch(() => setCameras([]));
  };

  useEffect(() => {
    refresh();
    return () => {
      if (debounce.current !== null) clearTimeout(debounce.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = () => {
    const next = mode === "grid" ? "white" : "grid";
    setMode(next);
    projectorShow({ mode: next }).catch(() => {});
  };

  const onExposure = (value: number) => {
    setExposure(value);
    if (!cameraId) return;
    if (debounce.current !== null) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      setCameraControls(cameraId, { auto_exposure: false, exposure: value })
        .then((r) => setExposureUnavailable(r.applied.exposure === false))
        .catch(() => {});
    }, DEBOUNCE_MS);
  };

  return (
    <section>
      <p>
        Place the camera next to the projector, pointing at the house. The whole grid must be inside
        the picture. Fix the camera so it cannot move.
      </p>
      {cameras !== null && cameras.length === 0 ? (
        <p>No camera found. Plug in a USB webcam and click Refresh.</p>
      ) : null}
      <div className={styles.row}>
        {cameras && cameras.length > 1 ? (
          <label>
            Camera{" "}
            <select value={cameraId ?? ""} onChange={(e) => onCameraChange(e.target.value || null)}>
              <option value="">Choose a camera…</option>
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <button type="button" onClick={refresh}>
          Refresh
        </button>
        <button type="button" onClick={toggle}>
          {mode === "grid" ? "Show white" : "Show grid"}
        </button>
      </div>
      {cameraId ? (
        <CropBox
          frame={shownFrame}
          initial={editRoi ?? wholePicture}
          suggested={check?.suggested_roi ?? null}
          active={editing && editRoi !== null}
          onUse={(next) => {
            onRoiChange(next);
            setEditing(false);
          }}
          onWhole={() => {
            onRoiChange(null);
            setEditing(false);
          }}
        >
          <LiveView
            cameraId={cameraId}
            paused={checking}
            onFrame={(width, height) =>
              setFrame((old) => (old?.width === width && old.height === height ? old : { width, height }))
            }
          />
        </CropBox>
      ) : null}
      {cameraId && !editing && roi ? (
        <div>
          {roiFromLastScan ? <p className={styles.note}>{COPY.cropFromLastScan}</p> : null}
          <p>{COPY.cropActive}</p>
          <CropPreview key={`${roi.x},${roi.y},${roi.w},${roi.h}`} cameraId={cameraId} roi={roi} />
          <div className={styles.row}>
            <button type="button" onClick={() => setEditing(true)}>
              {COPY.cropEdit}
            </button>
            <button type="button" onClick={() => onRoiChange(null)}>
              {COPY.cropWhole}
            </button>
          </div>
        </div>
      ) : null}
      {cameraId ? (
        <div className={styles.row}>
          <button
            type="button"
            className={check ? undefined : "primary"}
            disabled={checking}
            onClick={() => void runCheck()}
          >
            {COPY.checkButton}
          </button>
        </div>
      ) : null}
      {checking ? <p role="status">{COPY.checking}</p> : null}
      {checkError ? (
        <ErrorBanner
          message={checkError}
          actions={
            <button type="button" className="primary" onClick={() => void runCheck()}>
              {COPY.buttons.recheck}
            </button>
          }
        />
      ) : null}
      {check && !checking ? (
        <CameraCheckCard
          check={check}
          onCrop={() => {
            if (check.suggested_roi) onRoiChange(check.suggested_roi);
            setEditing(true);
          }}
          onUsePhone={onUsePhone}
          onNext={onNext}
          onRecheck={() => void runCheck()}
        />
      ) : null}
      {cameraId ? (
        <div className={styles.row}>
          <label>
            Exposure{" "}
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={exposure}
              onChange={(e) => onExposure(Number(e.target.value))}
            />
          </label>
        </div>
      ) : null}
      {exposureUnavailable ? (
        <p className={styles.note}>
          This camera does not allow exposure control. That is usually fine.
        </p>
      ) : null}
    </section>
  );
}
