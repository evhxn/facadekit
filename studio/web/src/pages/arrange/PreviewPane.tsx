import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, assetUrl, frameUrl, projectorShow, projectorTransport } from "../../api/client";
import { userMessage } from "../../api/errors";
import type { Project } from "../../api/types";
import { PreviewCompositor } from "../../gl/compositor";
import { useProjectStore } from "../../state/projectStore";
import { formatDuration } from "./MediaLibrary";
import { useTransportTime, type Transport } from "./transport";
import styles from "./PreviewPane.module.css";

const EXACT_DELAY_MS = 400;
const DRIFT_INTERVAL_MS = 2000;

interface Exact {
  url: string;
  loaded: boolean;
}

export default function PreviewPane({
  project,
  transport,
}: {
  project: Project;
  transport: Transport;
}) {
  const pid = project.id;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const compRef = useRef<PreviewCompositor | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const t = useTransportTime(transport);
  const [playing, setPlaying] = useState(transport.playing);
  const [house, setHouse] = useState(false);
  const houseRef = useRef(false);
  const [note, setNote] = useState<string | null>(null);
  const [exact, setExact] = useState<Exact | null>(null);

  // Clips whose surface is missing are excluded from the preview.
  const previewProject = useMemo(() => {
    const known = new Set(project.surfaces.map((s) => s.id));
    known.add("canvas");
    const copy = structuredClone(project);
    for (const track of copy.timeline.tracks) {
      track.clips = track.clips.filter((c) => known.has(c.surface_id));
    }
    return copy;
  }, [project]);

  // Everything except the revision: changes when the picture can change.
  const contentKey = useMemo(
    () => JSON.stringify([project.timeline, project.surfaces, project.assets, project.projector]),
    [project],
  );

  const audioId = project.timeline.audio_asset_id;
  const { width: pw, height: ph } = project.projector;

  // Compositor lifetime.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const comp = new PreviewCompositor(canvas, { assetUrl: (aid) => assetUrl(pid, aid) });
    compRef.current = comp;
    return () => {
      comp.dispose();
      compRef.current = null;
    };
  }, [pid]);

  // Re-render when the project changes (also while paused).
  useEffect(() => {
    const comp = compRef.current;
    if (!comp) return;
    comp.setProject(previewProject);
    comp.render(transport.tMs, transport.playing);
  }, [previewProject, pid, transport]);

  // Render on every tick, seek, play and pause.
  useEffect(
    () =>
      transport.onTick((ms) => {
        compRef.current?.render(ms, transport.playing);
        setPlaying(transport.playing);
      }),
    [transport],
  );

  // Animation frame loop while playing.
  useEffect(() => {
    if (!playing || typeof requestAnimationFrame !== "function") return;
    let raf = 0;
    const loop = () => {
      transport.tick();
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [playing, transport]);

  // Audio is the clock when the timeline has audio.
  useEffect(() => {
    transport.setAudio(audioId ? audioRef.current : null);
    return () => transport.setAudio(null);
  }, [audioId, transport]);

  // Space toggles play/pause.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "Space" && e.key !== " ") return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON") return;
      e.preventDefault();
      if (transport.playing) transport.pause();
      else transport.play();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [transport]);

  // Exact backend-rendered frame once paused for a moment.
  useEffect(() => {
    setExact(null);
    if (playing) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      void (async () => {
        try {
          await useProjectStore.getState().saveNow();
        } catch {
          // keep the WebGL preview
        }
        if (cancelled) return;
        const rev = useProjectStore.getState().project?.revision ?? 0;
        const base = frameUrl(pid, Math.round(t), 0.5);
        setExact({ url: `${base}${base.includes("?") ? "&" : "?"}rev=${rev}`, loaded: false });
      })();
    }, EXACT_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [playing, t, contentKey, pid]);

  // --- Show on house ---
  const failHouse = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.code === "PROJECTOR_NOT_CONNECTED") {
      houseRef.current = false;
      setHouse(false);
      setNote(userMessage(e.code, e.message));
    }
  }, []);

  const sendTransport = useCallback(async () => {
    if (!houseRef.current) return;
    const p = useProjectStore.getState().project;
    if (!p) return;
    try {
      await projectorTransport({
        project_id: p.id,
        playing: transport.playing,
        t_ms: Math.round(transport.tMs),
        revision: p.revision,
      });
    } catch (e) {
      failHouse(e);
    }
  }, [transport, failHouse]);

  async function toggleHouse(on: boolean) {
    setNote(null);
    if (!on) {
      houseRef.current = false;
      setHouse(false);
      await projectorShow({ mode: "blank" }).catch(() => {});
      return;
    }
    try {
      await useProjectStore.getState().saveNow();
      await projectorShow({ mode: "compositor", project_id: pid });
      houseRef.current = true;
      setHouse(true);
      await sendTransport();
    } catch (e) {
      failHouse(e);
    }
  }

  useEffect(() => transport.onChange(() => void sendTransport()), [transport, sendTransport]);

  useEffect(() => {
    if (house) void sendTransport();
  }, [project.revision, house, sendTransport]);

  useEffect(() => {
    if (!house || !playing) return;
    const id = setInterval(() => void sendTransport(), DRIFT_INTERVAL_MS);
    return () => clearInterval(id);
  }, [house, playing, sendTransport]);

  useEffect(
    () => () => {
      if (houseRef.current) {
        houseRef.current = false;
        void projectorShow({ mode: "blank" }).catch(() => {});
      }
    },
    [],
  );

  return (
    <section className={styles.pane} aria-label="Preview">
      <div className={styles.stage} style={{ aspectRatio: `${pw} / ${ph}` }}>
        <canvas
          ref={canvasRef}
          className={styles.canvas}
          width={Math.max(1, Math.round(pw / 2))}
          height={Math.max(1, Math.round(ph / 2))}
          aria-label="Preview canvas"
        />
        {exact ? (
          <img
            className={styles.exact}
            style={{ visibility: exact.loaded ? "visible" : "hidden" }}
            src={exact.url}
            alt="Exact rendered frame"
            onLoad={() => setExact((e) => (e ? { ...e, loaded: true } : e))}
            onError={() => setExact(null)}
          />
        ) : null}
        {exact?.loaded ? <span className={styles.badge}>exact</span> : null}
      </div>
      {audioId ? <audio ref={audioRef} src={assetUrl(pid, audioId)} preload="auto" /> : null}
      <div className={styles.transport}>
        <button type="button" onClick={() => (playing ? transport.pause() : transport.play())}>
          {playing ? "Pause" : "Play"}
        </button>
        <button
          type="button"
          onClick={() => {
            transport.pause();
            transport.seek(0);
          }}
        >
          Stop
        </button>
        <span className={styles.time} aria-label="Time">
          {formatDuration(t)}
        </span>
      </div>
      <label className={styles.house}>
        <input
          type="checkbox"
          checked={house}
          onChange={(e) => void toggleHouse(e.target.checked)}
        />
        Show on house
      </label>
      {note ? (
        <p role="alert" className={styles.note}>
          {note}
        </p>
      ) : null}
    </section>
  );
}
