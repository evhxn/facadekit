import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, projectorShow, projectorStatus, projViewUrl } from "../api/client";
import { newId } from "../api/types";
import type { Surface } from "../api/types";
import RemoteDialog from "../components/RemoteDialog";
import { useProjectStore } from "../state/projectStore";
import SurfaceCanvas from "./surfaces/SurfaceCanvas";
import MaskEditor from "./surfaces/MaskEditor";
import SurfaceList from "./surfaces/SurfaceList";
import { estimateAspect, flipHorizontal, rotateCorners, type Quad } from "./surfaces/geometry";
import styles from "./SurfacesPage.module.css";

const SHOW_DEBOUNCE_MS = 60;

export default function SurfacesPage() {
  const { projectId } = useParams();
  const project = useProjectStore((s) => s.project);
  const load = useProjectStore((s) => s.load);
  const update = useProjectStore((s) => s.update);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [corner, setCorner] = useState<number | null>(null);
  const [outlines, setOutlines] = useState(false);
  const [notConnected, setNotConnected] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [remoteOpen, setRemoteOpen] = useState(false);

  useEffect(() => {
    if (projectId && useProjectStore.getState().project?.id !== projectId) {
      load(projectId).catch(() => setLoadError("Could not load the project."));
    }
  }, [projectId, load]);

  // Default the outlines toggle to on when the projector window is open.
  useEffect(() => {
    let cancelled = false;
    projectorStatus()
      .then((s) => {
        if (!cancelled && s.connected) setOutlines(true);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // Live outlines: debounce, save, then show; one cycle at a time, always the last state.
  const sync = useRef({
    timer: null as ReturnType<typeof setTimeout> | null,
    running: false,
    again: false,
    dead: false,
  });
  const latest = useRef({ projectId: "", selectedId: null as string | null });
  latest.current = { projectId: project?.id ?? "", selectedId };

  const runSync = useCallback(async () => {
    const st = sync.current;
    if (st.running) {
      st.again = true;
      return;
    }
    st.running = true;
    try {
      do {
        st.again = false;
        await useProjectStore.getState().saveNow();
        if (st.dead) return;
        const { projectId: pid, selectedId: sel } = latest.current;
        await projectorShow({
          mode: "outlines",
          project_id: pid,
          ...(sel ? { selected_id: sel } : {}),
        });
      } while (st.again && !st.dead);
    } catch (e) {
      if (e instanceof ApiError && e.code === "PROJECTOR_NOT_CONNECTED") {
        setOutlines(false);
        setNotConnected(true);
      }
    } finally {
      st.running = false;
    }
  }, []);

  const surfaces = project?.surfaces;
  const shapeKey = useMemo(
    () => JSON.stringify((surfaces ?? []).map((s) => [s.id, s.quad])),
    [surfaces],
  );
  useEffect(() => {
    if (!outlines || !project) return;
    const st = sync.current;
    if (st.timer) clearTimeout(st.timer);
    st.timer = setTimeout(() => {
      st.timer = null;
      void runSync();
    }, SHOW_DEBOUNCE_MS);
    return () => {
      if (st.timer) clearTimeout(st.timer);
      st.timer = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outlines, shapeKey, selectedId, project?.id, runSync]);

  useEffect(() => {
    const st = sync.current;
    st.dead = false;
    return () => {
      st.dead = true;
      projectorShow({ mode: "blank" }).catch(() => {});
    };
  }, []);

  if (!project) {
    return <p>{loadError ?? "Loading…"}</p>;
  }
  const setupLink = `/p/${project.id}/setup`;
  if (!project.calibration_id) {
    return (
      <p>
        No calibration yet. Go to <Link to={setupLink}>Setup</Link> to scan the house first.
      </p>
    );
  }

  const selected = project.surfaces.find((s) => s.id === selectedId) ?? null;
  const { width, height } = project.projector;

  const patch = (id: string, fn: (s: Surface) => void) =>
    update((d) => {
      const s = d.surfaces.find((x) => x.id === id);
      if (s) fn(s);
    });

  const create = (quad: Quad) => {
    const id = newId("srf");
    update((d) => {
      d.surfaces.push({
        id,
        name: `Surface ${d.surfaces.length + 1}`,
        quad,
        mask: null,
        feather_px: 0,
        aspect: Math.round(estimateAspect(quad) * 100) / 100 || 1,
        mesh: null,
      });
    });
    setSelectedId(id);
    setCorner(null);
  };

  const remove = (id: string) => {
    const used = project.timeline.tracks.reduce(
      (n, t) => n + t.clips.filter((c) => c.surface_id === id).length,
      0,
    );
    if (used > 0) {
      const ok = window.confirm(
        `This surface is used by ${used} clip${used === 1 ? "" : "s"}. Deleting it removes them too. Continue?`,
      );
      if (!ok) return;
    }
    update((d) => {
      d.surfaces = d.surfaces.filter((s) => s.id !== id);
      for (const t of d.timeline.tracks) t.clips = t.clips.filter((c) => c.surface_id !== id);
    });
    if (selectedId === id) {
      setSelectedId(null);
      setCorner(null);
    }
  };

  return (
    <div className={styles.page}>
      <div className={styles.canvasArea}>
        <SurfaceCanvas
          imageUrl={projViewUrl(project.id, project.calibration_id)}
          imgW={width}
          imgH={height}
          surfaces={project.surfaces}
          selectedId={selectedId}
          corner={corner}
          onSelect={(id, c) => {
            setSelectedId(id);
            setCorner(c);
          }}
          onChangeQuad={(id, quad) => patch(id, (s) => (s.quad = quad))}
          onChangeMask={(id, mask) => patch(id, (s) => (s.mask = mask))}
          onChangeShape={(id, quad, mask) =>
            patch(id, (s) => {
              s.quad = quad;
              s.mask = mask;
            })
          }
          onCreate={create}
          onDelete={remove}
        />
      </div>
      <aside className={styles.panel}>
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={outlines}
            onChange={(e) => {
              setOutlines(e.target.checked);
              if (e.target.checked) setNotConnected(false);
            }}
          />{" "}
          Show outlines on projector
        </label>
        <button type="button" onClick={() => setRemoteOpen(true)}>
          Use phone as remote
        </button>
        {remoteOpen && <RemoteDialog projectId={project.id} onClose={() => setRemoteOpen(false)} />}
        {notConnected && (
          <p className={styles.note}>
            Projector window is not open. <Link to={setupLink}>Open Setup</Link>
          </p>
        )}
        <h2>Surfaces</h2>
        <SurfaceList
          surfaces={project.surfaces}
          selectedId={selectedId}
          onSelect={(id) => {
            setSelectedId(id);
            setCorner(null);
          }}
          onRename={(id, name) => patch(id, (s) => (s.name = name))}
          onDelete={remove}
        />
        {selected && (
          <Properties
            key={selected.id}
            surface={selected}
            onName={(name) => patch(selected.id, (s) => (s.name = name))}
            onAspect={(a) => patch(selected.id, (s) => (s.aspect = a))}
            onEstimate={() =>
              patch(
                selected.id,
                (s) => (s.aspect = Math.round(estimateAspect(s.quad) * 100) / 100 || 1),
              )
            }
            onFeather={(f) => patch(selected.id, (s) => (s.feather_px = f))}
            onFlip={() => patch(selected.id, (s) => (s.quad = flipHorizontal(s.quad)))}
            onRotate={() => patch(selected.id, (s) => (s.quad = rotateCorners(s.quad)))}
          />
        )}
        {selected && (
          <MaskEditor
            key={`shape-${selected.id}`}
            surface={selected}
            onChange={(mask) => patch(selected.id, (s) => (s.mask = mask))}
          />
        )}
      </aside>
    </div>
  );
}

interface PropsProps {
  surface: Surface;
  onName: (v: string) => void;
  onAspect: (v: number) => void;
  onEstimate: () => void;
  onFeather: (v: number) => void;
  onFlip: () => void;
  onRotate: () => void;
}

function Properties({
  surface,
  onName,
  onAspect,
  onEstimate,
  onFeather,
  onFlip,
  onRotate,
}: PropsProps) {
  const [aspectText, setAspectText] = useState(String(surface.aspect));
  useEffect(() => setAspectText(String(surface.aspect)), [surface.aspect]);
  return (
    <section className={styles.props}>
      <h2>Properties</h2>
      <label>
        Name
        <input value={surface.name} onChange={(e) => onName(e.target.value)} />
      </label>
      <label>
        Aspect
        <input
          type="number"
          step={0.01}
          min={0.01}
          value={aspectText}
          onChange={(e) => {
            setAspectText(e.target.value);
            const v = parseFloat(e.target.value);
            if (Number.isFinite(v) && v > 0) onAspect(v);
          }}
        />
      </label>
      <button type="button" onClick={onEstimate}>
        Estimate from shape
      </button>
      <small>Real width ÷ height. A 16 ft × 7 ft garage door is 2.29.</small>
      <label>
        Edge softness ({surface.feather_px} px)
        <input
          type="range"
          min={0}
          max={50}
          step={1}
          value={Math.min(50, surface.feather_px)}
          onChange={(e) => onFeather(Number(e.target.value))}
        />
      </label>
      <div className={styles.row}>
        <button type="button" onClick={onFlip}>
          Flip horizontally
        </button>
        <button type="button" onClick={onRotate}>
          Rotate corners
        </button>
      </div>
    </section>
  );
}
