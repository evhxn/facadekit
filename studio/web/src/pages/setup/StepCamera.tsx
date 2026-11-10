import { useEffect, useRef, useState } from "react";
import { cameraSnapshotUrl, listCameras, projectorShow, setCameraControls } from "../../api/client";
import type { CameraInfo } from "../../api/types";
import styles from "../SetupPage.module.css";

const REFRESH_MS = 350;
const DEBOUNCE_MS = 200;

interface Props {
  cameraId: string | null;
  onCameraChange: (id: string | null) => void;
}

function LiveView({ cameraId }: { cameraId: string }) {
  const [src, setSrc] = useState(() => `${cameraSnapshotUrl(cameraId)}?t=${Date.now()}`);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setSrc(`${cameraSnapshotUrl(cameraId)}?t=${Date.now()}`);
    return () => {
      if (timer.current !== null) clearTimeout(timer.current);
    };
  }, [cameraId]);

  // Next request starts only after the previous image finished (or failed) loading.
  const scheduleNext = () => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setSrc(`${cameraSnapshotUrl(cameraId)}?t=${Date.now()}`);
    }, REFRESH_MS);
  };

  return (
    <img
      className={styles.live}
      src={src}
      alt="Live camera view"
      onLoad={scheduleNext}
      onError={scheduleNext}
    />
  );
}

export default function StepCamera({ cameraId, onCameraChange }: Props) {
  const [cameras, setCameras] = useState<CameraInfo[] | null>(null);
  const [mode, setMode] = useState<"grid" | "white">("grid");
  const [exposure, setExposure] = useState(0.5);
  const [exposureUnavailable, setExposureUnavailable] = useState(false);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

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
      {cameraId ? <LiveView cameraId={cameraId} /> : null}
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
