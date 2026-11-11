import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
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
  const [piHost, setPiHost] = useState<string | null>(null); // FPP address when the projector is on the Pi
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    useProjectStore
      .getState()
      .load(projectId)
      .catch(() => setLoadError("Could not open this project."));
  }, [projectId]);

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
      {step === 2 ? <StepCamera cameraId={cameraId} onCameraChange={setCameraId} /> : null}
      {step === 3 ? <StepScan project={project} cameraId={cameraId} piHost={piHost} /> : null}
      {step === 3 && project.calibration_id && project.surfaces.length > 0 ? (
        <StepRealign project={project} cameraId={cameraId} />
      ) : null}
      <div className={styles.nav}>
        <button type="button" disabled={step === 1} onClick={() => setStep(step - 1)}>
          Back
        </button>
        {step < 3 ? (
          <button
            type="button"
            disabled={(step === 1 && !projectorReady) || (step === 2 && !cameraId)}
            onClick={() => setStep(step + 1)}
            className="primary"
          >
            Next
          </button>
        ) : null}
      </div>
    </main>
  );
}
