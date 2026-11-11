import { useEffect, useRef, useState } from "react";
import { fppProbe, health, projectorShow, projectorStatus } from "../../api/client";
import { connectEvents } from "../../api/events";
import { userMessage } from "../../api/errors";
import type { Project, ProjectorStatus } from "../../api/types";
import styles from "../SetupPage.module.css";

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
  const mismatch = connected && !matches;
  const ready = onPi ? piHost !== null : sim || matches;

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
      {sim ? <p className={styles.note}>Simulator mode: no projector needed</p> : null}
      <p>
        {connected
          ? `Projector connected (${status.width ?? "?"}×${status.height ?? "?"}).`
          : "Projector not connected."}
      </p>
      {mismatch ? (
        <p role="alert">
          {userMessage("RESOLUTION_MISMATCH", "The projector resolution does not match.")} The
          projector shows {status.width ?? "?"}×{status.height ?? "?"}, but this project needs {pw}×
          {ph}.
        </p>
      ) : null}
      {matches ? (
        <p>
          You should see a white grid on the house. The projector may take 2 minutes to reach full
          brightness.
        </p>
      ) : null}
    </section>
  );
}
