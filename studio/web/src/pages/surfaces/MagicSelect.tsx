import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, segment } from "../../api/client";
import type { SegmentPoint, SegmentResult } from "../../api/client";
import { userMessage } from "../../api/errors";
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

export interface MagicState {
  active: boolean;
  points: SegmentPoint[];
  result: SegmentResult | null;
  busy: boolean;
  error: string;
  note: string;
  disabledReason?: string;
  start: () => void;
  cancel: () => void;
  addPoint: (p: Pt, label: 0 | 1) => void;
  undo: () => void;
  accept: () => void;
}

export function useMagicSelect(magic: MagicProps | undefined): MagicState {
  const [active, setActive] = useState(false);
  const [points, setPoints] = useState<SegmentPoint[]>([]);
  const [result, setResult] = useState<SegmentResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const counter = useRef(0);
  const pid = magic?.projectId ?? "";

  const run = useCallback(
    async (pts: SegmentPoint[]) => {
      const id = ++counter.current;
      setBusy(true);
      setError("");
      let fellBack = false;
      try {
        const res = await segment(pid, pts).catch((e: unknown) => {
          if (!(e instanceof ApiError && e.code === "MODEL_DOWNLOAD_FAILED")) throw e;
          fellBack = true;
          return segment(pid, pts, "grabcut");
        });
        if (id !== counter.current) return;
        setResult(res);
        setNote(fellBack ? "Using the simple selector (model download failed)." : "");
      } catch (e) {
        if (id !== counter.current) return;
        setResult(null);
        setNote("");
        if (e instanceof ApiError && e.code === "BAD_REQUEST") {
          setError("Nothing found there. Try clicking the middle of the object.");
        } else if (e instanceof ApiError) {
          setError(userMessage(e.code, e.message));
        } else {
          setError("Something went wrong.");
        }
      } finally {
        if (id === counter.current) setBusy(false);
      }
    },
    [pid],
  );

  const reset = () => {
    counter.current++;
    setPoints([]);
    setResult(null);
    setBusy(false);
    setError("");
    setNote("");
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
    void run(next);
  };
  const undo = () => {
    if (points.length === 0) return;
    const next = points.slice(0, -1);
    setPoints(next);
    if (next.length === 0) {
      counter.current++;
      setResult(null);
      setBusy(false);
      setError("");
      setNote("");
    } else {
      void run(next);
    }
  };
  const accept = () => {
    if (!result || !magic) return;
    const quad = result.quad as Quad;
    magic.onAccept(quad, maskForResult(result.outer, quad));
    cancel();
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
    result,
    busy,
    error,
    note,
    disabledReason: magic?.disabledReason,
    start,
    cancel,
    addPoint,
    undo,
    accept,
  };
}

/** Toolbar controls for the Magic select tool. */
export function MagicControls({ ms }: { ms: MagicState }) {
  if (!ms.active) {
    return (
      <button
        type="button"
        onClick={ms.start}
        disabled={!!ms.disabledReason}
        title={ms.disabledReason ?? "Click an object to select it"}
      >
        Magic select
      </button>
    );
  }
  return (
    <>
      <button type="button" onClick={ms.accept} disabled={!ms.result || ms.busy}>
        Create surface
      </button>
      <button type="button" onClick={ms.undo} disabled={ms.points.length === 0}>
        Undo last point
      </button>
      <button type="button" onClick={ms.cancel}>
        Cancel
      </button>
      {ms.busy && (
        <span role="status">
          Working… First use downloads a small model, this can take a minute.
        </span>
      )}
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

/** Draw the prompt dots, the returned outline and the quad. */
export function drawMagic(ctx: CanvasRenderingContext2D, view: View, ms: MagicState): void {
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
