import { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { getCalibration, listCameras } from "../api/client";
import type { CameraCheck, CameraRoi } from "../api/types";
import { useProjectStore } from "../state/projectStore";
import styles from "./SetupPage.module.css";
import StepCamera from "./setup/StepCamera";
import StepProjector from "./setup/StepProjector";
import StepRealign from "./setup/StepRealign";
import StepScan from "./setup/StepScan";

const TITLES = ["Projector", "Camera", "Scan"];

export default function SetupPage() {
  const { projectId = "" } = useParams();
  const project = useProjectStore((s) => s.project);
  const [step, setStep] = useState<number | null>(null);
  const [projectorReady, setProjectorReady] = useState(false);
  const [cameraId, setCameraId] = useState<string | null>(null);
  const [roi, setRoi] = useState<CameraRoi | null>(null); // crop of the camera picture
  const [roiFromLastScan, setRoiFromLastScan] = useState(false);
  const roiRef = useRef(roi);
  roiRef.current = roi;
  const [check, setCheck] = useState<CameraCheck | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const phone = searchParams.get("phone") === "1";
  const [piHost, setPiHost] = useState<string | null>(null); // FPP address when the projector is on the Pi
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    useProjectStore
      .getState()
      .load(projectId)
      .catch(() => setLoadError("Could not open this project. Go back to Projects and open it again."));
  }, [projectId]);

  // A crop and a check result belong to one camera.
  const changeCamera = (id: string | null) => {
    if (id === cameraId) return;
    setCameraId(id);
    setRoi(null);
    setRoiFromLastScan(false);
    setCheck(null);
  };
  const changeRoi = (next: CameraRoi | null) => {
    setRoi(next);
    setRoiFromLastScan(false);
  };

  // Start from the crop of the latest scan when it was made with the chosen camera.
  const lastCalibrationId = project?.calibration_id ?? null;
  useEffect(() => {
    if (!cameraId || !lastCalibrationId) return;
    let cancelled = false;
    getCalibration(projectId, lastCalibrationId)
      .then((cal) => {
        const last = cal.settings?.roi;
        if (cancelled || !last || cal.camera_id !== cameraId) return;
        if (roiRef.current) return; // the user already chose a crop
        setRoi(last);
        setRoiFromLastScan(true);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [projectId, cameraId, lastCalibrationId]);

  // A calibrated project opens on the scan step, past the camera step. Choose the camera
  // there too: the one of the last scan if it is still plugged in, else the only one.
  // ("Scan again" without a camera used to be refused by the server with a bare 422.)
  useEffect(() => {
    if (!lastCalibrationId) return;
    let cancelled = false;
    void (async () => {
      try {
        const list = await listCameras();
        if (cancelled || !Array.isArray(list)) return;
        let pick = list.length === 1 ? list[0]?.id : undefined;
        try {
          const last = (await getCalibration(projectId, lastCalibrationId)).camera_id;
          if (last && list.some((c) => c.id === last)) pick = last;
        } catch {
          // No old scan to read: the only camera, if there is exactly one, still stands.
        }
        if (!cancelled && pick) setCameraId((current) => current ?? pick);
      } catch {
        // The camera step shows listing problems; here the user is simply asked to choose.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, lastCalibrationId]);

  const goScan = (viaPhone: boolean) => {
    setSearchParams(viaPhone ? { phone: "1" } : {}, { replace: true });
    setStep(3);
  };

  const loaded = project !== null && project.id === projectId;
  useEffect(() => {
    if (loaded && step === null) setStep(project.calibration_id ? 3 : 1);
  }, [loaded, step, project]);

  if (loadError) return <main className={styles.page}>{loadError}</main>;
  if (!loaded || step === null) return <main className={styles.page}>Loading…</main>;

  return (
    <main className={styles.page}>
      <h1>Set up “{project.name}”</h1>
      <p className={styles.steps}>
        Step {step} of 3: {TITLES[step - 1]}
      </p>
      {step === 1 ? (
        <StepProjector
          project={project}
          piHost={piHost}
          onPiHostChange={setPiHost}
          onReadyChange={setProjectorReady}
        />
      ) : null}
      {step === 2 ? (
        <StepCamera
          projectId={projectId}
          cameraId={cameraId}
          onCameraChange={changeCamera}
          roi={roi}
          onRoiChange={changeRoi}
          roiFromLastScan={roiFromLastScan}
          check={check}
          onCheckChange={setCheck}
          onNext={() => goScan(false)}
          onUsePhone={() => goScan(true)}
        />
      ) : null}
      {step === 3 ? (
        <StepScan
          project={project}
          cameraId={cameraId}
          piHost={piHost}
          roi={roi}
          onRoiReset={() => changeRoi(null)}
          phone={phone}
          onChooseCamera={() => setStep(2)}
        />
      ) : null}
      {step === 3 && project.calibration_id && project.surfaces.length > 0 ? (
        <StepRealign project={project} cameraId={cameraId} onChooseCamera={() => setStep(2)} />
      ) : null}
      <div className={styles.nav}>
        <button type="button" disabled={step === 1} onClick={() => setStep(step - 1)}>
          Back
        </button>
        {step < 3 ? (
          <button
            type="button"
            disabled={(step === 1 && !projectorReady) || (step === 2 && !cameraId)}
            onClick={() => (step === 2 ? goScan(false) : setStep(step + 1))}
            className="primary"
          >
            Next
          </button>
        ) : null}
      </div>
    </main>
  );
}
