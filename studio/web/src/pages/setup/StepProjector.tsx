import { useEffect, useRef, useState } from "react";
import { health, projectorShow, projectorStatus } from "../../api/client";
import { connectEvents } from "../../api/events";
import { userMessage } from "../../api/errors";
import type { Project, ProjectorStatus } from "../../api/types";
import styles from "../SetupPage.module.css";

interface Props {
  project: Project;
  onReadyChange: (ready: boolean) => void;
}

export default function StepProjector({ project, onReadyChange }: Props) {
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
  const ready = sim || matches;

  useEffect(() => {
    onReadyChange(ready);
  }, [ready, onReadyChange]);

  useEffect(() => {
    if (matches && !gridShown.current) {
      gridShown.current = true;
      projectorShow({ mode: "grid" }).catch(() => {});
    }
  }, [matches]);

  return (
    <section>
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
