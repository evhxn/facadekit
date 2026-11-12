import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, projectorShow } from "../api/client";
import { connectEvents } from "../api/events";
import { userMessage } from "../api/errors";
import PairForm from "../components/PairForm";
import { useProjectStore } from "../state/projectStore";
import styles from "./RemotePage.module.css";

const REPEAT_MS = 120;
const STEPS = [1, 5, 20];
type Target = 0 | 1 | 2 | 3 | "all";
const CORNERS: { label: string; target: Target }[] = [
  { label: "TL", target: 0 },
  { label: "TR", target: 1 },
  { label: "BL", target: 3 },
  { label: "BR", target: 2 },
];
const ARROWS = [
  { label: "Up", glyph: "▲", cls: styles.up, dx: 0, dy: -1 },
  { label: "Left", glyph: "◀", cls: styles.left, dx: -1, dy: 0 },
  { label: "Right", glyph: "▶", cls: styles.right, dx: 1, dy: 0 },
  { label: "Down", glyph: "▼", cls: styles.down, dx: 0, dy: 1 },
];

export default function RemotePage() {
  const { projectId } = useParams();
  const project = useProjectStore((s) => s.project);
  const dirty = useProjectStore((s) => s.dirty);
  const saving = useProjectStore((s) => s.saving);
  const saveError = useProjectStore((s) => s.error);
  const load = useProjectStore((s) => s.load);
  const update = useProjectStore((s) => s.update);

  const [needsPair, setNeedsPair] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [index, setIndex] = useState(0);
  const [target, setTarget] = useState<Target>(0);
  const [step, setStep] = useState(5);
  const [edited, setEdited] = useState(false);
  const [link, setLink] = useState<"ok" | "projector" | "offline">("ok");

  const tryLoad = useCallback(async () => {
    if (!projectId) return;
    try {
      await load(projectId);
      setNeedsPair(false);
      setLoadError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) setNeedsPair(true);
      else setLoadError(e instanceof ApiError ? userMessage(e.code, e.message) : "Could not load the project. Reload this page, or scan the QR code on the computer again.");
    }
  }, [projectId, load]);

  useEffect(() => {
    void tryLoad();
  }, [tryLoad]);

  // Reload when the project changed elsewhere (laptop edits), unless we have local edits.
  useEffect(() => {
    if (needsPair || !projectId) return;
    return connectEvents((msg) => {
      if (msg.type !== "project" || msg.project_id !== projectId) return;
      const s = useProjectStore.getState();
      if (s.dirty || s.saving || s.project?.revision === msg.revision) return;
      void load(projectId).catch(() => {});
    });
  }, [needsPair, projectId, load]);

  // Throttled projector update: one in flight, always the latest state afterwards.
  const show = useRef({ running: false, again: false });
  const runShow = useCallback(async () => {
    const st = show.current;
    if (st.running) {
      st.again = true;
      return;
    }
    st.running = true;
    try {
      do {
        st.again = false;
        await useProjectStore.getState().saveNow();
        const p = useProjectStore.getState().project;
        if (!p) return;
        const surface = p.surfaces[latest.current.index];
        try {
          await projectorShow({
            mode: "outlines",
            project_id: p.id,
            ...(surface ? { selected_id: surface.id } : {}),
          });
          setLink("ok");
        } catch (e) {
          setLink(e instanceof ApiError && e.code === "NETWORK" ? "offline" : "projector");
        }
      } while (st.again);
    } finally {
      st.running = false;
    }
  }, []);

  const surfaces = project?.surfaces ?? [];
  const idx = Math.min(index, Math.max(0, surfaces.length - 1));
  const latest = useRef({ index: idx, target, step });
  latest.current = { index: idx, target, step };

  const nudge = useCallback(
    (dx: number, dy: number) => {
      const { index: i, target: t, step: n } = latest.current;
      const s = useProjectStore.getState().project?.surfaces[i];
      if (!s) return;
      update((d) => {
        const quad = d.surfaces[i]?.quad;
        if (!quad) return;
        const corners = t === "all" ? [0, 1, 2, 3] : [t];
        for (const c of corners) {
          quad[c] = [quad[c]![0] + dx * n, quad[c]![1] + dy * n];
        }
      });
      setEdited(true);
      void runShow();
    },
    [update, runShow],
  );

  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const stop = useCallback(() => {
    if (timer.current !== null) {
      clearInterval(timer.current);
      timer.current = null;
    }
  }, []);
  const start = (dx: number, dy: number) => {
    stop();
    nudge(dx, dy);
    timer.current = setInterval(() => nudge(dx, dy), REPEAT_MS);
  };
  useEffect(() => stop, [stop]);

  const pickSurface = (delta: number) => {
    if (surfaces.length === 0) return;
    setIndex((idx + delta + surfaces.length) % surfaces.length);
    // Show the newly selected surface highlighted.
    setTimeout(() => void runShow(), 0);
  };

  if (needsPair) return <PairForm onPaired={tryLoad} />;
  if (!project) return <p>{loadError ?? "Loading…"}</p>;
  if (surfaces.length === 0) return <p>This project has no surfaces yet.</p>;

  const connection =
    link === "offline" ? "Offline" : link === "projector" ? "Projector not connected" : "Connected";
  const saveText = saveError ?? (dirty || saving ? "Saving…" : edited ? "Saved" : "");

  return (
    <div className={styles.page}>
      <div className={styles.status}>
        <span>{connection}</span>
        <span role="status">{saveText}</span>
      </div>
      <div className={styles.picker}>
        <button
          type="button"
          className={styles.big}
          aria-label="Previous surface"
          onClick={() => pickSurface(-1)}
        >
          ‹
        </button>
        <strong>{surfaces[idx]?.name}</strong>
        <button
          type="button"
          className={styles.big}
          aria-label="Next surface"
          onClick={() => pickSurface(1)}
        >
          ›
        </button>
      </div>
      <div className={styles.corners}>
        {CORNERS.map((c) => (
          <button
            key={c.label}
            type="button"
            className={`${styles.big} ${target === c.target ? styles.on : ""}`}
            aria-pressed={target === c.target}
            onClick={() => setTarget(c.target)}
          >
            {c.label}
          </button>
        ))}
      </div>
      <button
        type="button"
        className={`${styles.big} ${target === "all" ? styles.on : ""}`}
        aria-pressed={target === "all"}
        onClick={() => setTarget("all")}
      >
        All
      </button>
      <div className={styles.dpad}>
        {ARROWS.map((a) => (
          <button
            key={a.label}
            type="button"
            aria-label={a.label}
            className={`${styles.big} ${a.cls}`}
            onPointerDown={() => start(a.dx, a.dy)}
            onPointerUp={stop}
            onPointerLeave={stop}
            onPointerCancel={stop}
          >
            {a.glyph}
          </button>
        ))}
      </div>
      <div className={styles.steps}>
        {STEPS.map((n) => (
          <button
            key={n}
            type="button"
            className={`${styles.big} ${step === n ? styles.on : ""}`}
            aria-pressed={step === n}
            onClick={() => setStep(n)}
          >
            {n} px
          </button>
        ))}
      </div>
    </div>
  );
}
