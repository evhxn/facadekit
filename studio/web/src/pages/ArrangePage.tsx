import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { analyzeAsset, ApiError } from "../api/client";
import { waitForJob } from "../api/events";
import { defaultGenerator } from "../gl/generators";
import type { Asset, Clip } from "../api/types";
import { newId } from "../api/types";
import { useProjectStore } from "../state/projectStore";
import ClipForm, { CANVAS_ID } from "./arrange/ClipForm";
import ClipList from "./arrange/ClipList";
import {
  addClip,
  findClip,
  moveClipToTrack,
  removeClip,
  setShowAudio,
  updateClip,
} from "./arrange/clipOps";
import MediaLibrary from "./arrange/MediaLibrary";
import PreviewPane from "./arrange/PreviewPane";
import Timeline from "./arrange/Timeline";
import { Transport } from "./arrange/transport";
import styles from "./ArrangePage.module.css";

interface Draft {
  clip: Clip;
  layer: number | "new";
}

function beatError(code: string, message?: string): string {
  if (code === "AI_UNAVAILABLE") return "Beat detection is not installed. See the docs to add it.";
  return message || "Finding beats failed.";
}

export default function ArrangePage() {
  const { projectId } = useParams();
  const project = useProjectStore((s) => s.project);
  const load = useProjectStore((s) => s.load);
  const update = useProjectStore((s) => s.update);
  const [selection, setSelection] = useState<string[]>([]);
  const selectedId = selection.length === 1 ? (selection[0] ?? null) : null;
  const setSelectedId = (id: string | null) => setSelection(id ? [id] : []);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [beats, setBeats] = useState({ busy: false, progress: 0, error: null as string | null });
  const [loadError, setLoadError] = useState<string | null>(null);

  const durationRef = useRef(0);
  durationRef.current = project?.timeline.duration_ms ?? 0;
  const transport = useMemo(() => new Transport({ duration: () => durationRef.current }), []);

  useEffect(() => {
    if (projectId && useProjectStore.getState().project?.id !== projectId) {
      load(projectId).catch(() => setLoadError("Could not load the project. Go back to Projects and open it again."));
    }
  }, [projectId, load]);

  useEffect(() => () => transport.pause(), [transport]);

  // Undo / redo, everywhere on the page except while typing in a field.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.key.toLowerCase() !== "z") return;
      const el = e.target as HTMLElement | null;
      if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return;
      e.preventDefault();
      const s = useProjectStore.getState();
      if (e.shiftKey) s.redo();
      else s.undo();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!project) return <p>{loadError ?? "Loading…"}</p>;

  const firstVisual = project.assets.find((a) => a.kind !== "audio");
  const found = selectedId ? findClip(project, selectedId) : null;
  const formClip = draft?.clip ?? found?.clip ?? null;

  function startDraft() {
    if (!project) return;
    setSelectedId(null);
    setMessage(null);
    setDraft({
      layer: 0,
      clip: {
        id: newId("clp"),
        // No media yet: start from a solid effect so a project can be built without imports.
        asset_id: firstVisual ? firstVisual.id : null,
        generator: firstVisual ? null : defaultGenerator("solid"),
        surface_id: project.surfaces[0]?.id ?? CANVAS_ID,
        start_ms: 0,
        duration_ms: project.timeline.duration_ms > 0 ? project.timeline.duration_ms : 5000,
        in_offset_ms: 0,
        loop: true,
        fit: "fill",
        opacity: 1,
        fade_in_ms: 0,
        fade_out_ms: 0,
        blend: "normal",
      },
    });
  }

  function submitDraft() {
    if (!draft) return;
    const { clip, layer } = draft;
    update((p) => addClip(p, clip, layer));
    setDraft(null);
    setSelectedId(clip.id);
  }

  function patch(p: Partial<Clip>) {
    setMessage(null);
    if (draft) {
      setDraft({ ...draft, clip: { ...draft.clip, ...p } });
    } else if (selectedId) {
      const id = selectedId;
      update((proj) => updateClip(proj, id, p));
    }
  }

  function setLayer(layer: number | "new") {
    setMessage(null);
    if (draft) {
      setDraft({ ...draft, layer });
    } else if (selectedId) {
      const id = selectedId;
      let ok = true;
      update((proj) => {
        ok = moveClipToTrack(proj, id, layer);
      });
      if (!ok) setMessage("That layer already has a clip at this time. Drop it on another layer or pick a different time.");
    }
  }

  async function findBeats() {
    const pid = project?.id;
    const aid = project?.timeline.audio_asset_id;
    if (!pid || !aid || beats.busy) return;
    setBeats({ busy: true, progress: 0, error: null });
    try {
      await useProjectStore.getState().saveNow();
      const { job_id } = await analyzeAsset(pid, aid);
      const job = await waitForJob(job_id, (j) =>
        setBeats((b) => ({ ...b, progress: j.progress })),
      );
      if (job.state === "succeeded") {
        await load(pid);
        setBeats({ busy: false, progress: 1, error: null });
      } else {
        const code = job.error?.code ?? "";
        setBeats({ busy: false, progress: 0, error: beatError(code, job.error?.message) });
      }
    } catch (e) {
      const code = e instanceof ApiError ? e.code : "";
      setBeats({
        busy: false,
        progress: 0,
        error: beatError(code, e instanceof Error ? e.message : undefined),
      });
    }
  }

  function useAudio(asset: Asset | null) {
    update((p) => setShowAudio(p, asset));
  }

  const layer: number | "new" = draft
    ? draft.layer
    : found
      ? project.timeline.tracks.indexOf(found.track)
      : 0;

  return (
    <div className={styles.page}>
      <div className={styles.library}>
        <MediaLibrary
          projectId={project.id}
          assets={project.assets}
          audioAssetId={project.timeline.audio_asset_id}
          onUseAsAudio={useAudio}
        />
      </div>
      <div className={styles.centre}>
        <ClipList
          project={project}
          selectedId={selectedId}
          onSelect={(id) => {
            setDraft(null);
            setMessage(null);
            setSelectedId(id);
          }}
          onAdd={startDraft}
          onDuration={(ms) =>
            update((p) => {
              p.timeline.duration_ms = ms;
            })
          }
          onShowAudio={useAudio}
          onFindBeats={() => void findBeats()}
          beats={beats}
        />
        {formClip ? (
          <ClipForm
            clip={formClip}
            assets={project.assets}
            surfaces={project.surfaces}
            tracks={project.timeline.tracks}
            layer={layer}
            isNew={draft !== null}
            message={message}
            onPatch={patch}
            onLayer={setLayer}
            onSubmit={submitDraft}
            onCancel={() => {
              setDraft(null);
              setSelectedId(null);
            }}
            onRemove={() => {
              const id = formClip.id;
              update((p) => removeClip(p, id));
              setSelectedId(null);
            }}
          />
        ) : null}
      </div>
      <div className={styles.preview}>
        <PreviewPane project={project} transport={transport} />
      </div>
      <div className={styles.strip}>
        <Timeline
          project={project}
          transport={transport}
          selectedIds={selection}
          onSelect={(ids) => {
            setDraft(null);
            setSelection(ids);
          }}
          onOpen={(id) => {
            setDraft(null);
            setSelectedId(id);
          }}
        />
      </div>
    </div>
  );
}
