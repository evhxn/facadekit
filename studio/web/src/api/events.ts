import { getJob } from "./client";
import { isFinalJobState, type EventMessage, type Job } from "./types";

const MIN_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 5000;

/** Opens /ws/events, reconnecting with backoff 0.5 s -> 5 s. Returns a disposer. */
export function connectEvents(onMessage: (msg: EventMessage) => void): () => void {
  let ws: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let backoff = MIN_BACKOFF_MS;
  let disposed = false;

  const open = () => {
    if (disposed) return;
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${location.host}/ws/events`);
    ws = socket;
    socket.onopen = () => {
      backoff = MIN_BACKOFF_MS;
    };
    socket.onmessage = (ev: MessageEvent) => {
      try {
        onMessage(JSON.parse(String(ev.data)) as EventMessage);
      } catch {
        // ignore malformed messages
      }
    };
    socket.onclose = () => {
      if (disposed) return;
      timer = setTimeout(open, backoff);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
    };
    socket.onerror = () => socket.close();
  };

  open();

  return () => {
    disposed = true;
    if (timer !== null) clearTimeout(timer);
    if (ws) {
      ws.onclose = null;
      ws.close();
    }
  };
}

/** Resolves when the job reaches a final state. Events first, polling every 1 s as a fallback. */
export function waitForJob(jobId: string, onUpdate?: (job: Job) => void): Promise<Job> {
  return new Promise((resolve) => {
    let done = false;
    let polling = false;
    let stopEvents: () => void = () => {};
    let poll: ReturnType<typeof setInterval> | null = null;

    const finish = (fn: () => void) => {
      if (done) return;
      done = true;
      stopEvents();
      if (poll !== null) clearInterval(poll);
      fn();
    };
    const handle = (job: Job) => {
      if (done) return;
      onUpdate?.(job);
      if (isFinalJobState(job.state)) finish(() => resolve(job));
    };

    stopEvents = connectEvents((msg) => {
      if (msg.type === "job" && msg.job.id === jobId) handle(msg.job);
    });
    poll = setInterval(() => {
      if (polling) return;
      polling = true;
      getJob(jobId)
        .then(handle)
        .catch(() => {
          // transient errors: keep polling
        })
        .finally(() => {
          polling = false;
        });
    }, 1000);
  });
}
