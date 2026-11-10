import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { assetThumbUrl } from "../../api/client";
import type { Clip, Project } from "../../api/types";
import { newId } from "../../api/types";
import { useProjectStore } from "../../state/projectStore";
import { CANVAS_ID } from "./ClipForm";
import { assetName } from "./ClipList";
import { addClip, duplicateClips, findClip, moveClips, removeClip, trimClip } from "./clipOps";
import { ASSET_DRAG_TYPE } from "./MediaLibrary";
import { MAX_PX_PER_SEC, MIN_PX_PER_SEC, msToX, rulerTicks, snap, xToMs } from "./timelineMath";
import { useTransportTime, type Transport } from "./transport";
import styles from "./Timeline.module.css";

const ROW_H = 40;
const RULER_H = 22;
const SNAP_PX = 8;
const DEFAULT_PX_PER_SEC = 50;
const DEFAULT_IMAGE_MS = 5000;
const DRAG_THRESHOLD_PX = 3;

interface Ghost {
  start_ms: number;
  duration_ms: number;
  row: number;
}

type Session =
  | {
      mode: "move";
      primary: string;
      ids: string[];
      x0: number;
      y0: number;
      startRow: number;
      moved: boolean;
      shift: boolean;
      wasMulti: boolean;
      result: { dms: number; dtracks: number; valid: boolean } | null;
    }
  | {
      mode: "trim";
      id: string;
      edge: "start" | "end";
      x0: number;
      origMs: number;
      moved: boolean;
      newMs: number;
    }
  | { mode: "box"; x0: number; y0: number; moved: boolean; shift: boolean; ids: string[] }
  | { mode: "scrub" };

interface Props {
  project: Project;
  transport: Transport;
  selectedIds: string[];
  onSelect: (ids: string[]) => void;
  /** Double-click on a clip: open it in the clip form. */
  onOpen: (id: string) => void;
}

export default function Timeline({ project, transport, selectedIds, onSelect, onOpen }: Props) {
  const update = useProjectStore((s) => s.update);
  const t = useTransportTime(transport);
  const [pxPerSec, setPxPerSec] = useState(DEFAULT_PX_PER_SEC);
  const [ghosts, setGhosts] = useState<Map<string, Ghost> | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [box, setBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null);

  const rootRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const session = useRef<Session | null>(null);
  const zoomAnchor = useRef<{ ms: number; x: number } | null>(null);

  const projectRef = useRef(project);
  projectRef.current = project;
  const tRef = useRef(t);
  tRef.current = t;
  const ppm = pxPerSec / 1000;
  const ppmRef = useRef(ppm);
  ppmRef.current = ppm;

  const tracks = project.timeline.tracks;
  const nTracks = tracks.length;
  const duration = project.timeline.duration_ms;
  const width = Math.max(msToX(duration, ppm) + 80, 200);
  const height = RULER_H + (nTracks + 1) * ROW_H;
  // Display row 0 is the "new layer" row; rows 1..n are the tracks, top layer first.
  const rowOfTrack = (i: number) => nTracks - i;

  // Ctrl/Cmd + wheel zoom, centred on the cursor. Needs a non-passive listener.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const x = e.clientX - el.getBoundingClientRect().left;
      zoomAnchor.current = { ms: xToMs(x + el.scrollLeft, ppmRef.current), x };
      const factor = Math.exp(-e.deltaY * 0.0015);
      setPxPerSec((p) => Math.min(MAX_PX_PER_SEC, Math.max(MIN_PX_PER_SEC, p * factor)));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  useLayoutEffect(() => {
    const a = zoomAnchor.current;
    const el = scrollerRef.current;
    if (a && el) {
      el.scrollLeft = msToX(a.ms, ppm) - a.x;
      zoomAnchor.current = null;
    }
  }, [ppm]);

  function local(e: { clientX: number; clientY: number }) {
    const r = contentRef.current?.getBoundingClientRect();
    return { x: e.clientX - (r?.left ?? 0), y: e.clientY - (r?.top ?? 0) };
  }
  const rowAt = (y: number) => Math.min(nTracks, Math.max(0, Math.floor((y - RULER_H) / ROW_H)));

  function assetDuration(clip: Clip): number | null {
    const a = projectRef.current.assets.find((x) => x.id === clip.asset_id);
    return a && a.kind === "video" ? a.duration_ms : null;
  }

  function candidates(exclude: Set<string>): number[] {
    const p = projectRef.current;
    const c = [0, p.timeline.duration_ms, tRef.current];
    for (const tr of p.timeline.tracks) {
      for (const cl of tr.clips) {
        if (!exclude.has(cl.id)) c.push(cl.start_ms, cl.start_ms + cl.duration_ms);
      }
    }
    for (const m of p.markers) c.push(m.t_ms);
    return c;
  }

  function seekTo(clientX: number) {
    const ms = Math.max(0, xToMs(local({ clientX, clientY: 0 }).x, ppmRef.current));
    transport.seek(Math.min(ms, projectRef.current.timeline.duration_ms));
  }

  function capture(e: React.PointerEvent, el: Element) {
    try {
      el.setPointerCapture?.(e.pointerId);
    } catch {
      // not capturable (synthetic event): dragging still works while the pointer stays over us
    }
  }

  function onClipPointerDown(e: React.PointerEvent<HTMLDivElement>, clip: Clip) {
    if (e.button !== 0) return;
    e.stopPropagation();
    rootRef.current?.focus();
    const edge = (e.target as HTMLElement).dataset.edge as "start" | "end" | undefined;
    const wasSelected = selectedIds.includes(clip.id);
    let sel = selectedIds;
    if (e.shiftKey) {
      if (!wasSelected) sel = [...selectedIds, clip.id];
    } else if (!wasSelected) {
      sel = [clip.id];
    }
    if (sel !== selectedIds) onSelect(sel);
    capture(e, e.currentTarget);
    if (edge) {
      session.current = {
        mode: "trim",
        id: clip.id,
        edge,
        x0: e.clientX,
        origMs: edge === "start" ? clip.start_ms : clip.start_ms + clip.duration_ms,
        moved: false,
        newMs: 0,
      };
      return;
    }
    const found = findClip(projectRef.current, clip.id);
    const ti = found ? tracks.indexOf(found.track) : 0;
    session.current = {
      mode: "move",
      primary: clip.id,
      ids: sel,
      x0: e.clientX,
      y0: e.clientY,
      startRow: rowOfTrack(ti),
      moved: false,
      shift: e.shiftKey,
      wasMulti: wasSelected && sel.length > 1,
      result: null,
    };
  }

  function onContentPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    rootRef.current?.focus();
    capture(e, e.currentTarget);
    const { x, y } = local(e);
    if (y < RULER_H) {
      session.current = { mode: "scrub" };
      seekTo(e.clientX);
      return;
    }
    session.current = { mode: "box", x0: x, y0: y, moved: false, shift: e.shiftKey, ids: [] };
  }

  function onPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const s = session.current;
    if (!s) return;
    const p = projectRef.current;
    if (s.mode === "scrub") {
      seekTo(e.clientX);
    } else if (s.mode === "box") {
      const { x, y } = local(e);
      if (!s.moved && Math.hypot(x - s.x0, y - s.y0) < DRAG_THRESHOLD_PX) return;
      s.moved = true;
      const r = {
        x: Math.min(x, s.x0),
        y: Math.min(y, s.y0),
        w: Math.abs(x - s.x0),
        h: Math.abs(y - s.y0),
      };
      setBox(r);
      s.ids = [];
      p.timeline.tracks.forEach((tr, i) => {
        const top = RULER_H + rowOfTrack(i) * ROW_H;
        for (const c of tr.clips) {
          const left = msToX(c.start_ms, ppmRef.current);
          const right = msToX(c.start_ms + c.duration_ms, ppmRef.current);
          if (left < r.x + r.w && right > r.x && top < r.y + r.h && top + ROW_H > r.y)
            s.ids.push(c.id);
        }
      });
    } else if (s.mode === "trim") {
      const dx = e.clientX - s.x0;
      if (!s.moved && Math.abs(dx) < DRAG_THRESHOLD_PX) return;
      s.moved = true;
      let ms = s.origMs + xToMs(dx, ppmRef.current);
      if (!e.altKey) ms = snap(ms, candidates(new Set([s.id])), SNAP_PX, ppmRef.current);
      s.newMs = ms;
      const clone = structuredClone(p);
      const orig = findClip(p, s.id);
      if (!orig) return;
      trimClip(clone, s.id, s.edge, ms, assetDuration(orig.clip));
      const f = findClip(clone, s.id);
      if (!f) return;
      setInvalid(false);
      setGhosts(
        new Map([
          [
            s.id,
            {
              start_ms: f.clip.start_ms,
              duration_ms: f.clip.duration_ms,
              row: rowOfTrack(tracks.indexOf(orig.track)),
            },
          ],
        ]),
      );
    } else {
      const dx = e.clientX - s.x0;
      const dy = e.clientY - s.y0;
      if (!s.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
      s.moved = true;
      const origins = s.ids
        .map((id) => findClip(p, id))
        .filter((f): f is NonNullable<typeof f> => f !== null);
      const primary = origins.find((f) => f.clip.id === s.primary);
      if (!primary || origins.length === 0) return;
      let dms = xToMs(dx, ppmRef.current);
      dms = Math.max(dms, -Math.min(...origins.map((f) => f.clip.start_ms)));
      if (!e.altKey) {
        const cands = candidates(new Set(s.ids));
        const start = primary.clip.start_ms + dms;
        const end = start + primary.clip.duration_ms;
        const a = snap(start, cands, SNAP_PX, ppmRef.current) - start;
        const b = snap(end, cands, SNAP_PX, ppmRef.current) - end;
        const adj = a !== 0 && (b === 0 || Math.abs(a) <= Math.abs(b)) ? a : b;
        dms = Math.max(dms + adj, -Math.min(...origins.map((f) => f.clip.start_ms)));
      }
      dms = Math.round(dms);
      const dtracks = s.startRow - rowAt(local(e).y);
      const valid = moveClips(structuredClone(p), s.ids, dms, dtracks);
      s.result = { dms, dtracks, valid };
      const map = new Map<string, Ghost>();
      for (const f of origins) {
        const idx = tracks.indexOf(f.track) + dtracks;
        const row = idx >= nTracks ? 0 : idx < 0 ? nTracks : rowOfTrack(idx);
        map.set(f.clip.id, {
          start_ms: f.clip.start_ms + dms,
          duration_ms: f.clip.duration_ms,
          row,
        });
      }
      setInvalid(!valid);
      setGhosts(map);
    }
  }

  function onPointerUp(e: React.PointerEvent<HTMLDivElement>) {
    const s = session.current;
    session.current = null;
    setGhosts(null);
    setInvalid(false);
    setBox(null);
    if (!s || s.mode === "scrub") return;
    const p = projectRef.current;
    if (s.mode === "box") {
      if (!s.moved) {
        if (!s.shift) onSelect([]);
        seekTo(e.clientX);
      } else {
        onSelect(s.shift ? [...new Set([...selectedIds, ...s.ids])] : s.ids);
      }
    } else if (s.mode === "trim") {
      if (!s.moved) return;
      const orig = findClip(p, s.id);
      if (!orig) return;
      const { id, edge, newMs } = s;
      const dur = assetDuration(orig.clip);
      update((d) => trimClip(d, id, edge, newMs, dur));
    } else if (!s.moved) {
      if (s.wasMulti && !s.shift) onSelect([s.primary]);
    } else if (s.result?.valid && (s.result.dms !== 0 || s.result.dtracks !== 0)) {
      const { dms, dtracks } = s.result;
      const ids = s.ids;
      update((d) => {
        moveClips(d, ids, dms, dtracks);
      });
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (selectedIds.length === 0) return;
    const mod = e.ctrlKey || e.metaKey;
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      const ids = selectedIds;
      update((d) => {
        for (const id of ids) removeClip(d, id);
      });
      onSelect([]);
    } else if (mod && e.key.toLowerCase() === "d") {
      e.preventDefault();
      const ids = selectedIds;
      let created: string[] = [];
      update((d) => {
        created = duplicateClips(d, ids);
      });
      onSelect(created);
    }
  }

  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    const assetId = e.dataTransfer.getData(ASSET_DRAG_TYPE);
    if (!assetId) return;
    e.preventDefault();
    const p = projectRef.current;
    const asset = p.assets.find((a) => a.id === assetId);
    if (!asset || asset.kind === "audio") return;
    const { x, y } = local(e);
    const row = rowAt(y);
    const clip: Clip = {
      id: newId("clp"),
      asset_id: asset.id,
      generator: null,
      surface_id: p.surfaces[0]?.id ?? CANVAS_ID,
      start_ms: Math.max(0, Math.round(xToMs(x, ppmRef.current))),
      duration_ms:
        asset.duration_ms && asset.duration_ms > 0 ? asset.duration_ms : DEFAULT_IMAGE_MS,
      in_offset_ms: 0,
      loop: true,
      fit: "fill",
      opacity: 1,
      fade_in_ms: 0,
      fade_out_ms: 0,
      blend: "normal",
    };
    update((d) => addClip(d, clip, row === 0 ? "new" : nTracks - row));
    onSelect([clip.id]);
  }

  const ticks = rulerTicks(0, duration, ppm);

  return (
    <div
      ref={rootRef}
      className={styles.root}
      tabIndex={0}
      aria-label="Timeline"
      onKeyDown={onKeyDown}
    >
      <div ref={scrollerRef} className={styles.scroller}>
        <div
          ref={contentRef}
          className={styles.content}
          style={{ width, height }}
          onPointerDown={onContentPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onDragOver={(e) => {
            if (e.dataTransfer.types.includes(ASSET_DRAG_TYPE)) e.preventDefault();
          }}
          onDrop={onDrop}
        >
          <div className={styles.ruler} style={{ height: RULER_H }}>
            {ticks.map((tk) => (
              <span key={tk.ms} className={styles.tick} style={{ left: msToX(tk.ms, ppm) }}>
                {tk.label}
              </span>
            ))}
          </div>
          <div
            className={`${styles.row} ${styles.newRow}`}
            style={{ top: RULER_H, height: ROW_H }}
            data-testid="new-layer-row"
          >
            Drop here for a new layer
          </div>
          {[...tracks].reverse().map((track, k) => (
            <div
              key={track.id}
              className={styles.row}
              style={{ top: RULER_H + (k + 1) * ROW_H, height: ROW_H }}
              title={track.name}
            />
          ))}
          {tracks.flatMap((track, ti) =>
            track.clips.map((clip) => {
              const g = ghosts?.get(clip.id);
              const start = g ? g.start_ms : clip.start_ms;
              const dur = g ? g.duration_ms : clip.duration_ms;
              const row = g ? g.row : rowOfTrack(ti);
              const asset = project.assets.find((a) => a.id === clip.asset_id);
              return (
                <div
                  key={clip.id}
                  role="button"
                  aria-label={`Clip ${assetName(project, clip.asset_id)}`}
                  data-clip-id={clip.id}
                  className={[
                    styles.bar,
                    selectedIds.includes(clip.id) ? styles.selected : "",
                    g ? styles.ghost : "",
                    g && invalid ? styles.invalid : "",
                  ].join(" ")}
                  style={{
                    left: msToX(start, ppm),
                    width: Math.max(2, msToX(dur, ppm)),
                    top: RULER_H + row * ROW_H + 3,
                    height: ROW_H - 6,
                  }}
                  onPointerDown={(e) => onClipPointerDown(e, clip)}
                  onDoubleClick={(e) => {
                    e.stopPropagation();
                    onOpen(clip.id);
                  }}
                >
                  <span className={`${styles.handle} ${styles.handleStart}`} data-edge="start" />
                  {asset && asset.kind !== "audio" ? (
                    <img src={assetThumbUrl(project.id, asset.id)} alt="" draggable={false} />
                  ) : null}
                  <span className={styles.label}>{assetName(project, clip.asset_id)}</span>
                  <span className={`${styles.handle} ${styles.handleEnd}`} data-edge="end" />
                </div>
              );
            }),
          )}
          {box ? (
            <div
              className={styles.box}
              style={{ left: box.x, top: box.y, width: box.w, height: box.h }}
            />
          ) : null}
          <div className={styles.playhead} style={{ left: msToX(t, ppm) }} data-testid="playhead" />
        </div>
      </div>
    </div>
  );
}
