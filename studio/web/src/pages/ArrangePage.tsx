import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
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
import TimelineStrip from "./arrange/TimelineStrip";
import { Transport } from "./arrange/transport";
import styles from "./ArrangePage.module.css";

interface Draft {
  clip: Clip;
  layer: number | "new";
}

export default function ArrangePage() {
  const { projectId } = useParams();
  const project = useProjectStore((s) => s.project);
  const load = useProjectStore((s) => s.load);
  const update = useProjectStore((s) => s.update);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const durationRef = useRef(0);
  durationRef.current = project?.timeline.duration_ms ?? 0;
  const transport = useMemo(() => new Transport({ duration: () => durationRef.current }), []);

  useEffect(() => {
    if (projectId && useProjectStore.getState().project?.id !== projectId) {
      load(projectId).catch(() => setLoadError("Could not load the project."));
    }
  }, [projectId, load]);

  useEffect(() => () => transport.pause(), [transport]);

  if (!project) return <p>{loadError ?? "Loading…"}</p>;

  const firstVisual = project.assets.find((a) => a.kind !== "audio");
  const found = selectedId ? findClip(project, selectedId) : null;
  const formClip = draft?.clip ?? found?.clip ?? null;

  function startDraft() {
    if (!project || !firstVisual) return;
    setSelectedId(null);
    setMessage(null);
    setDraft({
      layer: 0,
      clip: {
        id: newId("clp"),
        asset_id: firstVisual.id,
        generator: null,
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
      if (!ok) setMessage("That layer already has a clip at this time.");
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
          canAdd={firstVisual !== undefined}
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
        <TimelineStrip
          project={project}
          transport={transport}
          selectedId={selectedId}
          onSelect={(id) => {
            setDraft(null);
            setSelectedId(id);
          }}
        />
      </div>
    </div>
  );
}
