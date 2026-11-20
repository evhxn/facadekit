import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  cameraAccess,
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
import LiveView from "./LiveView";

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

const KIND_NOTE: Record<string, string> = { built_in: "built in", phone: "phone" };

/** The device name, with a note for the kinds that are easy to pick by mistake. */
export function cameraLabel(camera: CameraInfo): string {
  const note = camera.kind ? KIND_NOTE[camera.kind] : undefined;
  return note && !camera.name.toLowerCase().includes(note)
    ? `${camera.name} (${note})`
    : camera.name;
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
  const [autofocus, setAutofocus] = useState(true);
  const [focusUnavailable, setFocusUnavailable] = useState(false);
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
    () =>
      frame ??
      (check
        ? { width: check.camera_width, height: check.camera_height }
        : { width: 1280, height: 720 }),
    [frame, check],
  );
  const editRoi = roi ?? check?.suggested_roi ?? null;
  const wholePicture = useMemo<CameraRoi>(
    () => ({
      x: 0,
      y: 0,
      w: shownFrame.width,
      h: shownFrame.height,
      frame_width: shownFrame.width,
      frame_height: shownFrame.height,
      bin: 1,
    }),
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

  const [listError, setListError] = useState<string | null>(null);
  const [accessMessage, setAccessMessage] = useState<string | null>(null);

  /** `look` also asks a system that finds cameras by opening them to search again. */
  const refresh = (look = false) => {
    setListError(null);
    listCameras(look)
      .then((list) => {
        setCameras(list);
        if (list.length === 1 && list[0] && !cameraId) onCameraChange(list[0].id);
        if (cameraId && !list.some((c) => c.id === cameraId)) onCameraChange(null);
      })
      .catch((e) => {
        setCameras(null);
        setListError(
          e instanceof ApiError
            ? userMessage(e.code, e.message)
            : "Could not ask FacadeKit for the cameras.",
        );
      });
    cameraAccess()
      .then((access) => setAccessMessage(access.message))
      .catch(() => setAccessMessage(null));
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

  const onAutofocus = (on: boolean) => {
    setAutofocus(on);
    if (!cameraId) return;
    setCameraControls(cameraId, { autofocus: on })
      .then((r) => setFocusUnavailable(r.applied.autofocus === false))
      .catch(() => {});
  };

  return (
    <section>
      {accessMessage ? <ErrorBanner message={accessMessage} /> : null}
      {listError ? <ErrorBanner message={listError} /> : null}

      <div className={styles.group}>
        <h3>1. Choose the camera</h3>
        {cameras === null && !listError ? <p role="status">Looking for cameras…</p> : null}
        {cameras !== null && cameras.length === 0 ? (
          <p>No camera found. Plug in a USB webcam and click Refresh.</p>
        ) : null}
        <div className={styles.row}>
          {cameras && cameras.length > 1 ? (
            <label>
              Camera{" "}
              <select
                value={cameraId ?? ""}
                onChange={(e) => onCameraChange(e.target.value || null)}
              >
                <option value="">Choose a camera…</option>
                {cameras.map((c) => (
                  <option key={c.id} value={c.id}>
                    {cameraLabel(c)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {cameras && cameras.length === 1 && cameras[0] ? (
            <span>Camera: {cameraLabel(cameras[0])}</span>
          ) : null}
          <button type="button" onClick={() => refresh(true)}>
            Refresh
          </button>
        </div>
      </div>

      {cameraId ? (
        <div className={styles.group}>
          <h3>2. Aim it at the house</h3>
          <p>
            Put the camera next to the projector, looking the same way. The whole grid must be
            inside the picture, with a little room around it. Fix the camera so it cannot move.
          </p>
          <div className={styles.row}>
            <button type="button" onClick={toggle}>
              {mode === "grid" ? "Project white" : "Project the grid"}
            </button>
          </div>
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
              onGone={() => {
                onCameraChange(null);
                refresh();
              }}
              onFrame={(width, height) =>
                setFrame((old) =>
                  old?.width === width && old.height === height ? old : { width, height },
                )
              }
            />
          </CropBox>
          {!editing && roi ? (
            <div>
              {roiFromLastScan ? <p className={styles.note}>{COPY.cropFromLastScan}</p> : null}
              <p>{COPY.cropActive}</p>
              <CropPreview
                key={`${roi.x},${roi.y},${roi.w},${roi.h}`}
                cameraId={cameraId}
                roi={roi}
              />
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
          <details>
            <summary>Camera settings</summary>
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
              <label>
                <input
                  type="checkbox"
                  checked={autofocus}
                  onChange={(e) => onAutofocus(e.target.checked)}
                />{" "}
                Autofocus
              </label>
            </div>
            {exposureUnavailable ? (
              <p className={styles.note}>
                This camera cannot be set to an exposure value, so this slider does nothing. The scan still locks the exposure where the camera allows it.
              </p>
            ) : null}
            {focusUnavailable ? (
              <p className={styles.note}>
                This camera does not let FacadeKit switch autofocus. Tap or turn its focus by hand
                if the picture is soft.
              </p>
            ) : null}
            <p className={styles.note}>
              The scan sets exposure and focus itself. These only change the live picture.
            </p>
          </details>
        </div>
      ) : null}

      {cameraId ? (
        <div className={styles.group}>
          <h3>3. Check the view</h3>
          <p className={styles.note}>
            Optional. The projector flashes and shows stripes for a few seconds, and FacadeKit says
            whether the camera sees enough for a good scan.
          </p>
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
        </div>
      ) : null}
    </section>
  );
}
