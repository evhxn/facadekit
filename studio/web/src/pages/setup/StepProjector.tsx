import { useEffect, useRef, useState } from "react";
import { fppProbe, health, projectorShow, projectorStatus } from "../../api/client";
import { connectEvents } from "../../api/events";
import type { Project, ProjectorStatus } from "../../api/types";
import { useProjectStore } from "../../state/projectStore";
import styles from "../SetupPage.module.css";

/** Sizes a projector really has; a window of any other size is just not full screen. */
const PROJECTOR_SIZES = new Set([
  "800x600", "1024x768", "1280x720", "1280x800", "1366x768", "1600x900",
  "1920x1080", "1920x1200", "2560x1440", "3840x2160",
]); // prettier-ignore

/** What to change when the projector window has the wrong number of pixels. */
export function mismatchAdvice(status: ProjectorStatus, pw: number, ph: number): string {
  const { width, height, dpr } = status;
  if (dpr !== null && Math.abs(dpr - 1) > 0.01) {
    return `The computer scales this display by ${dpr}. In the display settings, set the projector to ${pw}×${ph} with scaling off (100 %), and set the browser zoom of the projector window to 100 %.`;
  }
  if (width !== null && height !== null && width < pw && height < ph && !PROJECTOR_SIZES.has(`${width}x${height}`)) {
    return "The projector window is probably not full screen, or is still on the laptop screen. Drag it onto the projector, then click it to go full screen.";
  }
  return `Set the projector display to ${pw}×${ph} in the computer's display settings.`;
}

interface Props {
  project: Project;
  onReadyChange: (ready: boolean) => void;
  /** FPP address when the projector is on the Raspberry Pi; null = connected to this computer. */
  piHost: string | null;
  onPiHostChange: (host: string | null) => void;
}

export default function StepProjector({ project, onReadyChange, piHost, onPiHostChange }: Props) {
  const [onPi, setOnPi] = useState(piHost !== null);
  const [hostInput, setHostInput] = useState(piHost ?? "");
  const [probing, setProbing] = useState(false);
  const [probeProblem, setProbeProblem] = useState<string | null>(null);
  const [status, setStatus] = useState<ProjectorStatus | null>(null);
  const [sim, setSim] = useState(false);
  const gridShown = useRef(false);
  const { width: pw, height: ph } = project.projector;

  useEffect(() => {
    health()
      .then((h) => setSim(h.sim))
      .catch(() => {});
    projectorStatus()
      .then(setStatus)
      .catch(() => {});
    return connectEvents((msg) => {
      if (msg.type === "projector") setStatus(msg.status);
    });
  }, []);

  const connected = status?.connected === true;
  const matches = connected && status.width === pw && status.height === ph;
  // The simulated projector takes the project's resolution, so it cannot mismatch.
  const mismatch = connected && !matches && !sim;
  const ready = onPi ? piHost !== null : sim || matches;
  // A project nothing has been built on yet may simply take the projector's resolution.
  const untouched =
    project.calibration_id === null &&
    (project.surfaces?.length ?? 0) === 0 &&
    !(project.timeline?.tracks ?? []).some((t) => t.clips.length > 0);
  const adoptable =
    mismatch &&
    untouched &&
    status.width !== null &&
    status.height !== null &&
    (status.dpr === null || Math.abs(status.dpr - 1) <= 0.01) &&
    PROJECTOR_SIZES.has(`${status.width}x${status.height}`);
  const adopt = () => {
    if (!adoptable || status.width === null || status.height === null) return;
    const [width, height] = [status.width, status.height];
    useProjectStore.getState().update((draft) => {
      draft.projector.width = width;
      draft.projector.height = height;
    });
  };

  useEffect(() => {
    onReadyChange(ready);
  }, [ready, onReadyChange]);

  const probe = async () => {
    const host = hostInput.trim();
    setProbeProblem(null);
    onPiHostChange(null);
    if (!host) return;
    setProbing(true);
    try {
      const r = await fppProbe(host);
      if (r.ok) onPiHostChange(host);
      else setProbeProblem(r.problems.join(" ") || `Could not use FPP at ${host}.`);
    } catch {
      setProbeProblem(`Could not reach FPP at ${host}.`);
    } finally {
      setProbing(false);
    }
  };

  useEffect(() => {
    if (!onPi && matches && !gridShown.current) {
      gridShown.current = true;
      projectorShow({ mode: "grid" }).catch(() => {});
    }
  }, [matches, onPi]);

  const piChooser = (
    <fieldset>
      <legend>Projector is connected to:</legend>
      <label>
        <input
          type="radio"
          name="projector-source"
          checked={!onPi}
          onChange={() => {
            setOnPi(false);
            onPiHostChange(null);
          }}
        />{" "}
        this computer
      </label>{" "}
      <label>
        <input type="radio" name="projector-source" checked={onPi} onChange={() => setOnPi(true)} />{" "}
        my Raspberry Pi (FPP)
      </label>
    </fieldset>
  );

  if (onPi) {
    return (
      <section>
        {piChooser}
        <p>Enter the address of your Raspberry Pi running Falcon Player (FPP).</p>
        <div className={styles.row}>
          <input
            type="text"
            value={hostInput}
            placeholder="fpp.local"
            aria-label="FPP address"
            onChange={(e) => {
              setHostInput(e.target.value);
              onPiHostChange(null);
            }}
          />
          <button
            type="button"
            disabled={probing || !hostInput.trim()}
            onClick={() => void probe()}
          >
            {probing ? "Checking…" : "Check connection"}
          </button>
        </div>
        {piHost ? <p>FPP found at {piHost}.</p> : null}
        {probeProblem ? <p role="alert">{probeProblem}</p> : null}
        <p className={styles.note}>
          Turn off keystone and digital zoom on the projector. No projector window is needed: the
          scan plays from the Pi.
        </p>
      </section>
    );
  }

  return (
    <section>
      {piChooser}
      <ol>
        <li>Connect the projector to this computer with HDMI.</li>
        <li>Turn off keystone and digital zoom on the projector.</li>
        <li>
          Open the projector window and drag it onto the projector screen, then click it to go
          fullscreen.
        </li>
      </ol>
      <button
        type="button"
        onClick={() => window.open("/projector", "fk-projector", "popup,width=800,height=600")}
        className="primary"
      >
        Open projector window
      </button>
      {sim ? (
        <p className={styles.note}>
          Simulator mode: no projector needed. The simulated projector is {pw}×{ph}, like this
          project.
        </p>
      ) : (
        <p>
          {connected
            ? `Projector connected (${status.width ?? "?"}×${status.height ?? "?"}).`
            : "Projector not connected."}
        </p>
      )}
      {mismatch ? (
        <div role="alert">
          <p>
            The projector window is {status.width ?? "?"}×{status.height ?? "?"}, but this project
            needs {pw}×{ph}. {mismatchAdvice(status, pw, ph)}
          </p>
          {adoptable ? (
            <button type="button" onClick={adopt}>
              Use {status.width}×{status.height} for this project instead
            </button>
          ) : null}
        </div>
      ) : null}
      {matches && !sim ? (
        <p>
          You should see a white grid on the house. The projector may take 2 minutes to reach full
          brightness.
        </p>
      ) : null}
    </section>
  );
}
