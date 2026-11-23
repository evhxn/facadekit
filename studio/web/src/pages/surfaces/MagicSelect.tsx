import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, downloadSegmentModel, segment } from "../../api/client";
import type {
  SegmentBox,
  SegmentEngine,
  SegmentEngines,
  SegmentPoint,
  SegmentResult,
} from "../../api/client";
import { userMessage } from "../../api/errors";
import { waitForJob } from "../../api/events";
import type { Mask, Pt } from "../../api/types";
import type { Quad } from "./geometry";

export interface MagicProps {
  projectId: string;
  /** Set when the tool cannot be used (for example a manual calibration). */
  disabledReason?: string;
  /** Called with the accepted quad and mask (null when the outline is just the quad). */
  onAccept: (quad: Quad, mask: Mask | null) => void;
}

function area(poly: Pt[]): number {
  let a = 0;
  poly.forEach((p, i) => {
    const q = poly[(i + 1) % poly.length] as Pt;
    a += p[0] * q[1] - q[0] * p[1];
  });
  return Math.abs(a) / 2;
}

/** A mask is only worth storing when the outline is more than the quad itself. */
export function maskForResult(outer: Pt[], quad: Quad): Mask | null {
  const qa = area(quad);
  if (outer.length <= 4 && qa > 0 && Math.abs(area(outer) - qa) / qa <= 0.03) return null;
  return { outer, holes: [] };
}

/** "41 MB" (decimal megabytes, as download sizes are usually shown). */
export function formatMB(bytes: number): string {
  const mb = bytes / 1e6;
  return `${mb >= 10 ? Math.round(mb) : mb.toFixed(1)} MB`;
}

/** A box from two corners, in any order. Null when it is too small to mean anything. */
export function boxFrom(a: Pt, b: Pt, minPx = 3): SegmentBox | null {
  const box = {
    x0: Math.min(a[0], b[0]),
    y0: Math.min(a[1], b[1]),
    x1: Math.max(a[0], b[0]),
    y1: Math.max(a[1], b[1]),
  };
  return box.x1 - box.x0 < minPx || box.y1 - box.y0 < minPx ? null : box;
}

async function getJson(url: string): Promise<unknown> {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}

/**
 * Ask the backend whether the MobileSAM engine can run. Any failure counts as "no":
 * the simple selector is always available and needs no note to the user.
 * (Used only when the server does not answer /ai/segment/engines.)
 */
async function loadMobileSam(): Promise<boolean> {
  try {
    const body = (await getJson("/api/v1/ai/capabilities")) as { segment_engines?: unknown };
    return Array.isArray(body.segment_engines) && body.segment_engines.includes("mobilesam");
  } catch {
    return false;
  }
}

/** Which selector runs now and what each model would download. Null when unknown. */
async function loadEngines(): Promise<SegmentEngines | null> {
  try {
    const body = (await getJson("/api/v1/ai/segment/engines")) as Partial<SegmentEngines>;
    if (typeof body.active !== "string" || !Array.isArray(body.engines)) return null;
    return body as SegmentEngines;
  } catch {
    return null;
  }
}

export type DownloadState =
  | { phase: "idle" }
  | { phase: "confirm"; engine: SegmentEngine }
  | { phase: "running"; engine: SegmentEngine; progress: number }
  | { phase: "failed"; message: string };

export interface MagicState {
  active: boolean;
  points: SegmentPoint[];
  /** The box prompt ("Add rectangle" while Magic select is on). */
  box: SegmentBox | null;
  /** True while "Add rectangle" waits for a drag. */
  boxMode: boolean;
  result: SegmentResult | null;
  busy: boolean;
  error: string;
  note: string;
  /** null until the capabilities request has answered. */
  mobileSam: boolean | null;
  /** null until (or unless) the engine status has answered. */
  engines: SegmentEngines | null;
  /** The installed model in use, or null for the simple selector. */
  activeModel: SegmentEngine | null;
  /** A published model that can be downloaded (shown as "Download model (xx MB)"). */
  offer: SegmentEngine | null;
  download: DownloadState;
  disabledReason?: string;
  start: () => void;
  cancel: () => void;
  addPoint: (p: Pt, label: 0 | 1) => void;
  setBox: (box: SegmentBox | null) => void;
  toggleBoxMode: () => void;
  undo: () => void;
  accept: () => void;
  askDownload: () => void;
  confirmDownload: () => void;
  dismissDownload: () => void;
}

export function useMagicSelect(magic: MagicProps | undefined): MagicState {
  const [active, setActive] = useState(false);
  const [points, setPoints] = useState<SegmentPoint[]>([]);
  const [box, setBoxState] = useState<SegmentBox | null>(null);
  const [boxMode, setBoxMode] = useState(false);
  const [result, setResult] = useState<SegmentResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [mobileSam, setMobileSam] = useState<boolean | null>(null);
  const [engines, setEngines] = useState<SegmentEngines | null>(null);
  const [download, setDownload] = useState<DownloadState>({ phase: "idle" });
  const counter = useRef(0);
  const pid = magic?.projectId ?? "";

  useEffect(() => {
    let alive = true;
    void loadMobileSam().then((ok) => {
      if (alive) setMobileSam(ok);
    });
    void loadEngines().then((status) => {
      if (alive) setEngines(status);
    });
    return () => {
      alive = false;
    };
  }, []);

  const activeModel =
    engines && engines.active !== "grabcut"
      ? (engines.engines.find((e) => e.id === engines.active) ?? null)
      : null;
  const offer =
    engines && engines.onnxruntime && !activeModel
      ? (engines.engines.find((e) => e.published && !e.installed) ?? null)
      : null;

  const run = useCallback(
    async (pts: SegmentPoint[], bx: SegmentBox | null) => {
      const id = ++counter.current;
      setBusy(true);
      setError("");
      let fellBack = false;
      try {
        // With the engine status known, ask for what is installed (GrabCut needs no model).
        // Older servers: without MobileSAM there is nothing to load, so ask for GrabCut.
        const useModel = engines ? engines.active !== "grabcut" : mobileSam === true;
        const prefer = useModel ? undefined : "grabcut";
        const res = await segment(pid, pts, prefer, bx).catch((e: unknown) => {
          const modelTrouble =
            e instanceof ApiError &&
            (e.code === "MODEL_DOWNLOAD_FAILED" || e.code === "AI_UNAVAILABLE");
          if (!modelTrouble || prefer === "grabcut") throw e;
          fellBack = true;
          return segment(pid, pts, "grabcut", bx);
        });
        if (id !== counter.current) return;
        setResult(res);
        setNote(
          fellBack
            ? "Using the simple selector because the smart model could not be used. Try Magic select again later."
            : "",
        );
      } catch (e) {
        if (id !== counter.current) return;
        setResult(null);
        setNote("");
        if (e instanceof ApiError && e.code === "BAD_REQUEST") {
          setError("Nothing found there. Try clicking the middle of the object.");
        } else if (e instanceof ApiError) {
          setError(userMessage(e.code, e.message));
        } else {
          setError("Something went wrong. Click the object again, or draw the surface by hand.");
        }
      } finally {
        if (id === counter.current) setBusy(false);
      }
    },
    [pid, mobileSam, engines],
  );

  const clearResult = () => {
    counter.current++;
    setResult(null);
    setBusy(false);
    setError("");
    setNote("");
  };

  const reset = () => {
    clearResult();
    setPoints([]);
    setBoxState(null);
    setBoxMode(false);
  };

  const start = () => {
    reset();
    setActive(true);
  };
  const cancel = () => {
    reset();
    setActive(false);
  };
  const addPoint = (p: Pt, label: 0 | 1) => {
    const next = [...points, { x: p[0], y: p[1], label }];
    setPoints(next);
    void run(next, box);
  };
  const setBox = (bx: SegmentBox | null) => {
    setBoxState(bx);
    setBoxMode(false);
    if (bx || points.some((p) => p.label === 1)) void run(points, bx);
    else clearResult();
  };
  const toggleBoxMode = () => setBoxMode((v) => !v);
  const undo = () => {
    if (points.length === 0) {
      if (box) setBox(null);
      return;
    }
    const next = points.slice(0, -1);
    setPoints(next);
    if (!box && !next.some((p) => p.label === 1)) clearResult();
    else void run(next, box);
  };
  const accept = () => {
    if (!result || !magic) return;
    const quad = result.quad as Quad;
    magic.onAccept(quad, maskForResult(result.outer, quad));
    cancel();
  };

  const askDownload = () => {
    if (offer) setDownload({ phase: "confirm", engine: offer });
  };
  const dismissDownload = () => setDownload({ phase: "idle" });
  const confirmDownload = () => {
    if (download.phase !== "confirm") return;
    const engine = download.engine;
    setDownload({ phase: "running", engine, progress: 0 });
    void (async () => {
      try {
        const { job_id } = await downloadSegmentModel(engine.id, engine.download_bytes);
        const job = await waitForJob(job_id, (j) =>
          setDownload({ phase: "running", engine, progress: j.progress }),
        );
        if (job.state !== "succeeded") {
          const message = job.error
            ? userMessage(job.error.code, job.error.message)
            : "The download did not finish.";
          setDownload({ phase: "failed", message });
          return;
        }
        setEngines(await loadEngines());
        setDownload({ phase: "idle" });
        setNote(`${engine.label} is ready. Click the object again to use it.`);
      } catch (e) {
        setDownload({
          phase: "failed",
          message: e instanceof ApiError ? userMessage(e.code, e.message) : "The download failed.",
        });
      }
    })();
  };

  // Keyboard: Enter creates, Backspace undoes, Esc cancels.
  const keys = useRef({ accept, undo, cancel });
  keys.current = { accept, undo, cancel };
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT"))
        return;
      if (e.key === "Enter") {
        e.preventDefault();
        keys.current.accept();
      } else if (e.key === "Backspace") {
        e.preventDefault();
        keys.current.undo();
      } else if (e.key === "Escape") {
        keys.current.cancel();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);

  return {
    active,
    points,
    box,
    boxMode,
    result,
    busy,
    error,
    note,
    mobileSam,
    engines,
    activeModel,
    offer,
    download,
    disabledReason: magic?.disabledReason,
    start,
    cancel,
    addPoint,
    setBox,
    toggleBoxMode,
    undo,
    accept,
    askDownload,
    confirmDownload,
    dismissDownload,
  };
}

/** Which selector Magic select uses, said plainly. */
function EngineBadge({ ms }: { ms: MagicState }) {
  if (ms.activeModel) {
    return (
      <span
        aria-label="Active selector"
        title={`Smart select runs the ${ms.activeModel.label} model on this computer.`}
      >
        Smart select ({ms.activeModel.label})
      </span>
    );
  }
  // Without the engine status (older server) and with MobileSAM listed, say nothing.
  if (!ms.engines && ms.mobileSam !== false) return null;
  return (
    <span
      aria-label="Active selector"
      title="A simple colour-based tool picks the object. It can grab neighbours of a similar colour; a rectangle around the object helps."
    >
      <span>Simple selector</span> (GrabCut)
    </span>
  );
}

function DownloadControls({ ms }: { ms: MagicState }) {
  const d = ms.download;
  if (d.phase === "confirm") {
    const e = d.engine;
    const files = e.files.filter((f) => !f.installed);
    const hosts = [...new Set(files.map((f) => f.host))];
    return (
      <div role="dialog" aria-label="Download smart select model">
        <p>
          Download {e.label} ({formatMB(e.download_bytes)}) from {hosts.join(", ")}?
        </p>
        <ul>
          {files.map((f) => (
            <li key={f.name}>
              <code>{f.url}</code> ({formatMB(f.size_bytes)})
            </li>
          ))}
        </ul>
        <p>
          <small>
            Licence: {e.licence || "see the source"}. Each file is checked against its SHA-256
            before use. Nothing is downloaded until you press Download.
          </small>
        </p>
        <button type="button" onClick={ms.confirmDownload}>
          Download
        </button>
        <button type="button" onClick={ms.dismissDownload}>
          Not now
        </button>
      </div>
    );
  }
  if (d.phase === "running") {
    return (
      <span role="status">
        Downloading {d.engine.label}… {Math.round(d.progress * 100)} %
      </span>
    );
  }
  return (
    <>
      {d.phase === "failed" && <span role="alert">{d.message}</span>}
      {ms.offer && (
        <button
          type="button"
          onClick={ms.askDownload}
          title="A smart model picks objects by shape, not only colour. You will see the size and source before anything is downloaded."
        >
          Download model ({formatMB(ms.offer.download_bytes)})
        </button>
      )}
    </>
  );
}

/** Toolbar controls for the Magic select tool. */
export function MagicControls({ ms }: { ms: MagicState }) {
  if (!ms.active) {
    return (
      <>
        <button
          type="button"
          onClick={ms.start}
          disabled={!!ms.disabledReason}
          title={ms.disabledReason ?? "Click an object to select it"}
        >
          Magic select
        </button>
        <EngineBadge ms={ms} />
        {!ms.disabledReason && <DownloadControls ms={ms} />}
      </>
    );
  }
  return (
    <>
      <button type="button" onClick={ms.accept} disabled={!ms.result || ms.busy}>
        Create surface
      </button>
      <button type="button" onClick={ms.undo} disabled={ms.points.length === 0 && !ms.box}>
        Undo last point
      </button>
      <button type="button" onClick={ms.cancel}>
        Cancel
      </button>
      <EngineBadge ms={ms} />
      <DownloadControls ms={ms} />
      {ms.busy && <span role="status">Working…</span>}
      {ms.result && <span aria-label="Selection engine">{ms.result.engine}</span>}
      {ms.note && <small>{ms.note}</small>}
      {ms.error && <span role="alert">{ms.error}</span>}
    </>
  );
}

interface View {
  toScreen: (p: Pt) => Pt;
  scale: number;
}

/** Draw the prompt dots and box, the returned outline and the quad. */
export function drawMagic(
  ctx: CanvasRenderingContext2D,
  view: View,
  ms: MagicState,
  draftBox?: SegmentBox | null,
): void {
  const r = ms.result;
  if (r) {
    ctx.beginPath();
    r.outer.forEach((p, i) => {
      const s = view.toScreen(p);
      if (i === 0) ctx.moveTo(s[0], s[1]);
      else ctx.lineTo(s[0], s[1]);
    });
    ctx.closePath();
    ctx.fillStyle = "rgba(255,160,40,0.3)";
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#ffa028";
    ctx.stroke();
    ctx.beginPath();
    r.quad.forEach((p, i) => {
      const s = view.toScreen(p);
      if (i === 0) ctx.moveTo(s[0], s[1]);
      else ctx.lineTo(s[0], s[1]);
    });
    ctx.closePath();
    ctx.setLineDash([6, 4]);
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
    ctx.setLineDash([]);
    for (const p of r.quad) {
      const s = view.toScreen(p);
      ctx.beginPath();
      ctx.arc(s[0], s[1], 4, 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff";
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "#000";
      ctx.stroke();
    }
  }
  const bx = draftBox ?? ms.box;
  if (bx) {
    const a = view.toScreen([bx.x0, bx.y0]);
    const b = view.toScreen([bx.x1, bx.y1]);
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#30c0ff";
    ctx.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
    ctx.setLineDash([]);
  }
  for (const p of ms.points) {
    const s = view.toScreen([p.x, p.y]);
    ctx.beginPath();
    ctx.arc(s[0], s[1], 6, 0, Math.PI * 2);
    ctx.fillStyle = p.label === 1 ? "#30d060" : "#ff3030";
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#fff";
    ctx.stroke();
  }
}
