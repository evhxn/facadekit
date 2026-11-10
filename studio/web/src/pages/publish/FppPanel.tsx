import { useEffect, useRef, useState } from "react";
import { ApiError, cancelJob, fppProbe, fppStatus, publish, startRender } from "../../api/client";
import { waitForJob } from "../../api/events";
import { userMessage } from "../../api/errors";
import type { FppProbe, FppStatus, Job, Project } from "../../api/types";
import ErrorBanner from "../../components/ErrorBanner";
import JobProgress from "../../components/JobProgress";
import { useProjectStore } from "../../state/projectStore";
import styles from "./Panels.module.css";

const POLL_MS = 3000;

export function normalizeHost(raw: string): string {
  return raw
    .trim()
    .replace(/^[a-z][a-z0-9+.-]*:\/\//i, "")
    .replace(/\/+$/, "");
}

function failureMessage(job: Job, fallback: string): string {
  return job.error ? userMessage(job.error.code, job.error.message) : fallback;
}

export default function FppPanel({
  project,
  selectedRenderId,
}: {
  project: Project;
  selectedRenderId: string | null;
}) {
  const defaultName = `FacadeKit - ${project.name}`;
  const [hostInput, setHostInput] = useState(project.fpp.host ?? "");
  const [connected, setConnected] = useState<FppProbe | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [playlist, setPlaylist] = useState(defaultName);
  const [startNow, setStartNow] = useState(true);
  const [sendJob, setSendJob] = useState<Job | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<FppStatus | null>(null);
  const [alignJob, setAlignJob] = useState<Job | null>(null);
  const [alignStage, setAlignStage] = useState("");
  const [alignError, setAlignError] = useState<string | null>(null);
  const [alignDone, setAlignDone] = useState(false);
  const [aligning, setAligning] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const host = normalizeHost(hostInput);
  const isConnected = connected !== null && connected.ok;

  // Poll the player status while connected and the tab is visible.
  useEffect(() => {
    if (!isConnected || !host) {
      setStatus(null);
      return;
    }
    let timer: ReturnType<typeof setInterval> | null = null;
    let alive = true;
    const tick = () => {
      fppStatus(host)
        .then((s) => alive && setStatus(s))
        .catch(() => alive && setStatus(null));
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const start = () => {
      if (timer !== null) return;
      tick();
      timer = setInterval(tick, POLL_MS);
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") start();
      else stop();
    };
    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      alive = false;
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [isConnected, host]);

  const connect = async () => {
    setConnectError(null);
    setProblems([]);
    setConnected(null);
    setHostInput(host);
    try {
      const probe = await fppProbe(host);
      if (!mounted.current) return;
      if (probe.ok) {
        setConnected(probe);
        useProjectStore.getState().update((d) => {
          d.fpp.host = host;
        });
      } else {
        setProblems(
          probe.problems.length > 0
            ? probe.problems
            : [userMessage("FPP_UNREACHABLE", "The Falcon Player (FPP) could not be reached.")],
        );
      }
    } catch (e) {
      if (mounted.current)
        setConnectError(
          e instanceof ApiError ? userMessage(e.code, e.message) : "The connection failed.",
        );
    }
  };

  const reloadProject = () =>
    useProjectStore
      .getState()
      .load(project.id)
      .catch(() => {});

  const send = async () => {
    if (!selectedRenderId) return;
    setSendError(null);
    setSent(null);
    setSending(true);
    setSendJob(null);
    try {
      const body: { host: string; render_id: string; playlist_name?: string; start: boolean } = {
        host,
        render_id: selectedRenderId,
        start: startNow,
      };
      if (playlist !== defaultName) body.playlist_name = playlist;
      const { job_id } = await publish(project.id, body);
      const final = await waitForJob(job_id, (j) => mounted.current && setSendJob(j));
      if (!mounted.current) return;
      setSendJob(null);
      if (final.state === "succeeded") {
        const name =
          typeof final.result?.playlist_name === "string" ? final.result.playlist_name : playlist;
        setSent(name);
        await reloadProject();
      } else if (final.state === "failed") {
        setSendError(failureMessage(final, "Sending to FPP failed."));
      }
    } catch (e) {
      if (mounted.current)
        setSendError(e instanceof ApiError ? userMessage(e.code, e.message) : "Sending failed.");
    } finally {
      if (mounted.current) setSending(false);
    }
  };

  const alignmentCheck = async () => {
    setAlignError(null);
    setAlignDone(false);
    setAligning(true);
    setAlignJob(null);
    try {
      await useProjectStore.getState().saveNow();
      setAlignStage("Rendering the alignment check");
      const r = await startRender(project.id, {
        preset: "pi3-1080p30",
        include_audio: false,
        kind: "alignment_check",
      });
      const rendered = await waitForJob(r.job_id, (j) => mounted.current && setAlignJob(j));
      if (!mounted.current) return;
      if (rendered.state !== "succeeded") {
        setAlignJob(null);
        if (rendered.state === "failed")
          setAlignError(failureMessage(rendered, "Rendering the alignment check failed."));
        return;
      }
      const rid = typeof rendered.result?.render_id === "string" ? rendered.result.render_id : "";
      setAlignStage("Sending the alignment check to FPP");
      setAlignJob(null);
      const p = await publish(project.id, { host, render_id: rid, start: true });
      const sentJob = await waitForJob(p.job_id, (j) => mounted.current && setAlignJob(j));
      if (!mounted.current) return;
      setAlignJob(null);
      if (sentJob.state === "succeeded") {
        setAlignDone(true);
        await reloadProject();
      } else if (sentJob.state === "failed") {
        setAlignError(failureMessage(sentJob, "Sending the alignment check failed."));
      }
    } catch (e) {
      if (mounted.current)
        setAlignError(
          e instanceof ApiError ? userMessage(e.code, e.message) : "The alignment check failed.",
        );
    } finally {
      if (mounted.current) setAligning(false);
    }
  };

  const cancel = (job: Job) => () => void cancelJob(job.id).catch(() => {});

  return (
    <>
      <section className={styles.panel}>
        <h2>Send to Falcon Player</h2>
        <div className={styles.row}>
          <label>
            FPP address{" "}
            <input
              className={styles.input}
              value={hostInput}
              placeholder="fpp.local"
              onChange={(e) => {
                setHostInput(e.target.value);
                setConnected(null);
                setProblems([]);
              }}
            />
          </label>
          <button type="button" onClick={() => void connect()} disabled={host === ""}>
            Connect
          </button>
        </div>
        {connectError ? <ErrorBanner message={connectError} /> : null}
        {problems.length > 0 ? <ErrorBanner message={problems.join(" ")} /> : null}
        {isConnected ? (
          <p className={styles.ok}>Connected to FPP {connected.version ?? ""}</p>
        ) : null}
        {isConnected && status ? (
          <p>
            {status.current_playlist ? `FPP is playing ${status.current_playlist}` : "FPP is idle"}
          </p>
        ) : null}
        <div className={styles.row}>
          <label>
            Playlist name{" "}
            <input
              className={styles.input}
              value={playlist}
              onChange={(e) => setPlaylist(e.target.value)}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={startNow}
              onChange={(e) => setStartNow(e.target.checked)}
            />{" "}
            Start playing now
          </label>
          <button
            type="button"
            onClick={() => void send()}
            disabled={!isConnected || !selectedRenderId || sending || aligning}
          >
            Send to FPP
          </button>
        </div>
        {sendJob ? <JobProgress job={sendJob} onCancel={cancel(sendJob)} /> : null}
        {sendError ? <ErrorBanner message={sendError} /> : null}
        {sent !== null ? (
          <>
            <p className={styles.ok}>{`Sent. Playlist “${sent}” is on your FPP.`}</p>
            <p className={styles.reminder}>
              Move the projector's HDMI cable from this computer to the Raspberry Pi.
            </p>
          </>
        ) : null}
      </section>

      <section className={styles.panel}>
        <h2>Check alignment from the Pi</h2>
        <p className={styles.note}>
          A 20-second video of the surface outlines. If they sit on the real surfaces when played
          from the Pi, the show will line up too.
        </p>
        <button
          type="button"
          onClick={() => void alignmentCheck()}
          disabled={!isConnected || sending || aligning}
        >
          Render and send alignment check
        </button>
        {aligning && alignStage ? <p>{alignStage}</p> : null}
        {alignJob ? <JobProgress job={alignJob} onCancel={cancel(alignJob)} /> : null}
        {alignError ? <ErrorBanner message={alignError} /> : null}
        {alignDone ? (
          <p className={styles.reminder}>
            Look at the house. The outlines should sit exactly on your surfaces. If they are
            shifted, set FPP's HDMI output to the projector's native resolution, turn off overscan
            on the projector, then try again.
          </p>
        ) : null}
      </section>
    </>
  );
}
