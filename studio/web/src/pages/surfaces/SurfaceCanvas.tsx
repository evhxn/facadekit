import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as RPointerEvent } from "react";
import type { Mask, Mesh, Pt, Surface } from "../../api/types";
import {
  clampPt,
  hitTestCorner,
  isSelfIntersecting,
  makeView,
  moveQuadClamped,
  pointInQuad,
  rectQuad,
  type Quad,
} from "./geometry";
import { addHole, maskIsSimple, removeMaskVertex, translateMask } from "./MaskEditor";
import { controlPoint, gridPolylines, setControlPoint } from "./meshMath";
import { boxFrom, drawMagic, MagicControls, useMagicSelect, type MagicProps } from "./MagicSelect";
import type { SegmentBox } from "../../api/client";
import { insertVertex, nearestEdge, pointInPolygon } from "./polygon";
import InfoTip from "../../components/InfoTip";
import styles from "./SurfaceCanvas.module.css";

function setVertex(m: Mask, ring: number, index: number, v: Pt): Mask {
  if (ring < 0) return { ...m, outer: m.outer.map((q, i) => (i === index ? v : q)) };
  return {
    ...m,
    holes: m.holes.map((h, k) => (k === ring ? h.map((q, i) => (i === index ? v : q)) : h)),
  };
}

function isMeshCorner(mesh: Mesh, p: { i: number; j: number }): boolean {
  return (p.i === 0 || p.i === mesh.rows) && (p.j === 0 || p.j === mesh.cols);
}

const CORNER_NAMES = ["top-left", "top-right", "bottom-right", "bottom-left"];
// Big enough to see on a photo and to grab with a trackpad or a finger.
const HANDLE = 12;
const HIT_RADIUS = 14;
const ZOOM_STEP = 1.25;
const TIP_KEY = "fk.tip.surfaces";

function tipSeen(): boolean {
  try {
    return window.localStorage.getItem(TIP_KEY) === "1";
  } catch {
    return false;
  }
}
const MIN_ZOOM = 1;
const MAX_ZOOM = 8;

interface Props {
  imageUrl: string;
  imgW: number;
  imgH: number;
  surfaces: Surface[];
  selectedId: string | null;
  corner: number | null;
  onSelect: (id: string | null, corner: number | null) => void;
  onChangeQuad: (id: string, quad: Quad) => void;
  /** Mask edits, and moving a whole surface (quad and mask together). */
  onChangeMask: (id: string, mask: Mask) => void;
  onChangeMesh?: (id: string, mesh: Mesh) => void;
  onChangeShape: (id: string, quad: Quad, mask: Mask | null) => void;
  onCreate: (quad: Quad) => void;
  onDelete: (id: string) => void;
  /** Enables the Magic select tool. */
  magic?: MagicProps;
}

type Drag =
  | { kind: "corner"; id: string; index: number }
  | { kind: "move"; id: string; start: Pt; quad: Quad; mask: Mask | null }
  | { kind: "vertex"; id: string; ring: number; index: number }
  | { kind: "mesh"; id: string; i: number; j: number }
  | { kind: "pan"; start: Pt; panX: number; panY: number }
  | { kind: "mbox"; start: Pt };

export default function SurfaceCanvas(props: Props) {
  const { imageUrl, imgW, imgH, surfaces, selectedId, corner } = props;
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const spaceRef = useRef(false);
  const [size, setSize] = useState({ w: 800, h: 450 });
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [loaded, setLoaded] = useState(0);
  const [adding, setAdding] = useState<Pt[] | null>(null);
  const [cursor, setCursor] = useState<Pt | null>(null);
  const [editMask, setEditMask] = useState(false);
  const [vsel, setVsel] = useState<{ ring: number; index: number } | null>(null);
  const [holeDraft, setHoleDraft] = useState<Pt[] | null>(null);
  const [editMesh, setEditMesh] = useState(false);
  const [msel, setMsel] = useState<{ i: number; j: number } | null>(null);
  const [notice, setNotice] = useState("");
  const [showTip, setShowTip] = useState(() => !tipSeen());
  const ms = useMagicSelect(props.magic);
  const [draftBox, setDraftBox] = useState<SegmentBox | null>(null);
  const selectedSurface = surfaces.find((x) => x.id === selectedId);
  const hasMask = !!selectedSurface?.mask;
  const hasMesh = !!selectedSurface?.mesh;
  const meshMode = editMesh && hasMesh;

  // A surface with a custom outline opens in outline editing; a bent one in mesh editing.
  useEffect(() => {
    setEditMask(hasMask && !hasMesh);
    setEditMesh(hasMesh);
    setMsel(null);
    setVsel(null);
    setHoleDraft(null);
    setNotice("");
  }, [selectedId, hasMask, hasMesh]);

  const view = makeView({
    imgW,
    imgH,
    viewW: size.w,
    viewH: size.h,
    zoom,
    panX: pan.x,
    panY: pan.y,
  });
  const viewRef = useRef(view);
  viewRef.current = view;

  /** Zoom by a factor around a point on the canvas (the centre for the buttons). */
  const zoomBy = useCallback(
    (factor: number, at?: Pt) => {
      const sx = at ? at[0] : size.w / 2;
      const sy = at ? at[1] : size.h / 2;
      setZoom((z) => {
        const nz = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z * factor));
        if (nz === z) return z;
        const anchor = viewRef.current.toProjector([sx, sy]);
        const base = makeView({
          imgW,
          imgH,
          viewW: size.w,
          viewH: size.h,
          zoom: nz,
          panX: 0,
          panY: 0,
        });
        const p = base.toScreen(anchor);
        setPan(nz === MIN_ZOOM ? { x: 0, y: 0 } : { x: sx - p[0], y: sy - p[1] });
        return nz;
      });
    },
    [imgW, imgH, size.w, size.h],
  );
  const resetView = () => {
    setZoom(MIN_ZOOM);
    setPan({ x: 0, y: 0 });
  };
  const dismissTip = () => {
    setShowTip(false);
    try {
      window.localStorage.setItem(TIP_KEY, "1");
    } catch {
      // Storage blocked: the tip simply shows again next time.
    }
  };

  // Load the photo once per URL.
  useEffect(() => {
    const img = new Image();
    img.onload = () => {
      imgRef.current = img;
      setLoaded((n) => n + 1);
    };
    img.src = imageUrl;
    return () => {
      img.onload = null;
    };
  }, [imageUrl]);

  // Track the container size.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) setSize({ w: r.width, h: r.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Wheel zoom centred on the cursor (needs a non-passive listener).
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      zoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15, [e.clientX - rect.left, e.clientY - rect.top]);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [zoomBy]);

  // Esc cancels adding, even when focus left the canvas.
  useEffect(() => {
    if (!adding && !holeDraft) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setAdding(null);
        setHoleDraft(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [adding, holeDraft]);

  // Draw.
  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext?.("2d");
    if (!canvas || !ctx) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(size.w * dpr);
    canvas.height = Math.round(size.h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.w, size.h);
    ctx.fillStyle = "#111";
    ctx.fillRect(0, 0, size.w, size.h);
    const img = imgRef.current;
    if (img) {
      const a = view.toScreen([0, 0]);
      const b = view.toScreen([imgW, imgH]);
      ctx.drawImage(img, a[0], a[1], b[0] - a[0], b[1] - a[1]);
    }
    const path = (pts: Pt[], close: boolean) => {
      ctx.beginPath();
      pts.forEach((p, i) => {
        const s = view.toScreen(p);
        if (i === 0) ctx.moveTo(s[0], s[1]);
        else ctx.lineTo(s[0], s[1]);
      });
      if (close) ctx.closePath();
    };
    const stroke = (color: string) => {
      ctx.lineJoin = "round";
      ctx.strokeStyle = "rgba(0,0,0,0.75)";
      ctx.lineWidth = 6;
      ctx.stroke();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.stroke();
    };
    for (const s of surfaces) {
      const selected = s.id === selectedId;
      const crossed = isSelfIntersecting(s.quad);
      const m = s.mask;
      const editing = selected && editMask && !!m;
      if (m) {
        ctx.beginPath();
        for (const ring of [m.outer, ...m.holes]) {
          ring.forEach((p, i) => {
            const q = view.toScreen(p);
            if (i === 0) ctx.moveTo(q[0], q[1]);
            else ctx.lineTo(q[0], q[1]);
          });
          ctx.closePath();
        }
        if (selected) {
          ctx.fillStyle = "rgba(80,160,255,0.25)";
          ctx.fill("evenodd");
        }
        stroke(maskIsSimple(m) ? "#ffffff" : "#ff3030");
        // The mapping quad stays visible as a thin dashed line.
        path(s.quad, true);
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;
        ctx.strokeStyle = crossed ? "#ff3030" : "rgba(255,255,255,0.7)";
        ctx.stroke();
        ctx.setLineDash([]);
      } else {
        path(s.quad, true);
        if (selected) {
          ctx.fillStyle = "rgba(80,160,255,0.25)";
          ctx.fill();
        }
        stroke(crossed ? "#ff3030" : "#ffffff");
      }
      const cx = s.quad.reduce((t, p) => t + p[0], 0) / 4;
      const cy = s.quad.reduce((t, p) => t + p[1], 0) / 4;
      const c = view.toScreen([cx, cy]);
      ctx.font = "13px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.lineWidth = 3;
      ctx.strokeStyle = "rgba(0,0,0,0.8)";
      ctx.strokeText(s.name, c[0], c[1]);
      ctx.fillStyle = "#fff";
      ctx.fillText(s.name, c[0], c[1]);
      if (editing && m) {
        [m.outer, ...m.holes].forEach((ring, r) => {
          ring.forEach((p, i) => {
            const q = view.toScreen(p);
            ctx.beginPath();
            ctx.arc(q[0], q[1], HANDLE / 2, 0, Math.PI * 2);
            ctx.fillStyle = vsel && vsel.ring === r - 1 && vsel.index === i ? "#ffcc00" : "#ffffff";
            ctx.fill();
            ctx.lineWidth = 1.5;
            ctx.strokeStyle = "#000";
            ctx.stroke();
          });
        });
      }
      const meshEditing = selected && meshMode && !!s.mesh;
      if (meshEditing && s.mesh) {
        // Quad shown thin and dashed; the warped grid and its handles are what you edit.
        path(s.quad, true);
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;
        ctx.strokeStyle = "rgba(255,255,255,0.6)";
        ctx.stroke();
        ctx.setLineDash([]);
        for (const line of gridPolylines(s)) {
          path(line, false);
          stroke("#40e0ff");
        }
        for (let i = 0; i <= s.mesh.rows; i++) {
          for (let j = 0; j <= s.mesh.cols; j++) {
            const q = view.toScreen(controlPoint(s, i, j));
            ctx.beginPath();
            ctx.arc(q[0], q[1], HANDLE / 2, 0, Math.PI * 2);
            ctx.fillStyle = msel && msel.i === i && msel.j === j ? "#ffcc00" : "#ffffff";
            ctx.fill();
            ctx.lineWidth = 1.5;
            ctx.strokeStyle = "#000";
            ctx.stroke();
          }
        }
      }
      if (!editing && !meshEditing)
        s.quad.forEach((p, i) => {
          const q = view.toScreen(p);
          ctx.beginPath();
          if (i === 0) ctx.arc(q[0], q[1], HANDLE / 2 + 1, 0, Math.PI * 2);
          else ctx.rect(q[0] - HANDLE / 2, q[1] - HANDLE / 2, HANDLE, HANDLE);
          ctx.fillStyle = selected && corner === i ? "#ffcc00" : "#ffffff";
          ctx.fill();
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = "#000";
          ctx.stroke();
        });
    }
    if (holeDraft && holeDraft.length > 0) {
      path(cursor ? [...holeDraft, cursor] : holeDraft, false);
      stroke("#ffcc00");
      const f = view.toScreen(holeDraft[0] as Pt);
      ctx.beginPath();
      ctx.arc(f[0], f[1], HANDLE / 2 + 1, 0, Math.PI * 2);
      ctx.fillStyle = "#ffcc00";
      ctx.fill();
    }
    if (ms.active) drawMagic(ctx, view, ms, draftBox);
    if (adding && adding.length > 0) {
      const pts = cursor ? [...adding, cursor] : adding;
      path(pts, false);
      stroke("#ffcc00");
    }
  }, [
    view,
    size,
    surfaces,
    selectedId,
    corner,
    adding,
    cursor,
    loaded,
    imgW,
    imgH,
    editMask,
    meshMode,
    msel,
    vsel,
    holeDraft,
    ms,
    draftBox,
  ]);

  const toolPoint = useCallback(
    (e: { clientX: number; clientY: number }): Pt => {
      const canvas = canvasRef.current;
      const rect = canvas?.getBoundingClientRect();
      if (!rect || rect.width === 0 || rect.height === 0) return [e.clientX, e.clientY];
      return [
        ((e.clientX - rect.left) * size.w) / rect.width,
        ((e.clientY - rect.top) * size.h) / rect.height,
      ];
    },
    [size.w, size.h],
  );

  const hit = (sp: Pt): { id: string; corner: number | null } | null => {
    const radius = HIT_RADIUS / view.scale;
    const p = view.toProjector(sp);
    const selected = surfaces.find((s) => s.id === selectedId);
    if (selected) {
      const i = hitTestCorner(p, selected.quad, radius);
      if (i >= 0) return { id: selected.id, corner: i };
    }
    for (const s of [...surfaces].reverse()) {
      const i = hitTestCorner(p, s.quad, radius);
      if (i >= 0) return { id: s.id, corner: i };
    }
    if (selected && pointInQuad(p, selected.quad)) return { id: selected.id, corner: null };
    for (const s of [...surfaces].reverse()) {
      if (pointInQuad(p, s.quad)) return { id: s.id, corner: null };
    }
    return null;
  };

  const onPointerDown = (e: RPointerEvent<HTMLCanvasElement>) => {
    canvasRef.current?.focus();
    const sp = toolPoint(e);
    if (e.button === 1 || (e.button === 0 && spaceRef.current)) {
      e.preventDefault();
      dragRef.current = { kind: "pan", start: sp, panX: pan.x, panY: pan.y };
      canvasRef.current?.setPointerCapture?.(e.pointerId);
      return;
    }
    if (ms.active) {
      if (ms.boxMode && e.button === 0) {
        // "Add rectangle" in Magic select: drag a box prompt around the object.
        dragRef.current = { kind: "mbox", start: clampPt(view.toProjector(sp), imgW, imgH) };
        canvasRef.current?.setPointerCapture?.(e.pointerId);
        return;
      }
      if (e.button === 0 || e.button === 2) {
        const p = clampPt(view.toProjector(sp), imgW, imgH);
        ms.addPoint(p, e.button === 2 || e.altKey ? 0 : 1);
      }
      return;
    }
    if (e.button !== 0) return;
    if (adding) {
      const p = clampPt(view.toProjector(sp), imgW, imgH);
      const next = [...adding, p];
      if (next.length === 4) {
        setAdding(null);
        props.onCreate(next as Quad);
      } else {
        setAdding(next);
      }
      return;
    }
    if (holeDraft) {
      const p = clampPt(view.toProjector(sp), imgW, imgH);
      const first = holeDraft[0];
      if (
        first &&
        holeDraft.length >= 3 &&
        Math.hypot(p[0] - first[0], p[1] - first[1]) * view.scale <= HIT_RADIUS
      ) {
        closeHole(holeDraft);
      } else {
        setHoleDraft([...holeDraft, p]);
      }
      return;
    }
    if (selectedSurface?.mesh && meshMode) {
      const p = view.toProjector(sp);
      const radius = HIT_RADIUS / view.scale;
      const mesh = selectedSurface.mesh;
      let best: { i: number; j: number; d: number } | null = null;
      for (let i = 0; i <= mesh.rows; i++) {
        for (let j = 0; j <= mesh.cols; j++) {
          const c = controlPoint(selectedSurface, i, j);
          const d = Math.hypot(p[0] - c[0], p[1] - c[1]);
          if (d <= radius && (!best || d < best.d)) best = { i, j, d };
        }
      }
      if (best) {
        setMsel({ i: best.i, j: best.j });
        dragRef.current = { kind: "mesh", id: selectedSurface.id, i: best.i, j: best.j };
        canvasRef.current?.setPointerCapture?.(e.pointerId);
        return;
      }
      // Inside the surface: do nothing (no accidental moves while bending).
      if (pointInQuad(p, selectedSurface.quad)) {
        setMsel(null);
        return;
      }
    }
    if (selectedSurface?.mask && editMask) {
      const m = selectedSurface.mask;
      const p = view.toProjector(sp);
      const radius = HIT_RADIUS / view.scale;
      const rings = [m.outer, ...m.holes];
      for (let r = 0; r < rings.length; r++) {
        const ring = rings[r] as Pt[];
        for (let i = 0; i < ring.length; i++) {
          const v = ring[i] as Pt;
          if (Math.hypot(p[0] - v[0], p[1] - v[1]) <= radius) {
            setVsel({ ring: r - 1, index: i });
            dragRef.current = { kind: "vertex", id: selectedSurface.id, ring: r - 1, index: i };
            canvasRef.current?.setPointerCapture?.(e.pointerId);
            return;
          }
        }
      }
      let best: { r: number; index: number; point: Pt; distance: number } | null = null;
      for (let r = 0; r < rings.length; r++) {
        const ne = nearestEdge(rings[r] as Pt[], p);
        if (ne.distance <= radius * 0.7 && (!best || ne.distance < best.distance)) {
          best = { r, index: ne.index, point: ne.point, distance: ne.distance };
        }
      }
      if (best) {
        const next: Mask =
          best.r === 0
            ? { ...m, outer: insertVertex(m.outer, best.index, best.point) }
            : {
                ...m,
                holes: m.holes.map((hl, k) =>
                  k === best.r - 1 ? insertVertex(hl, best.index, best.point) : hl,
                ),
              };
        props.onChangeMask(selectedSurface.id, next);
        setVsel({ ring: best.r - 1, index: best.index + 1 });
        dragRef.current = {
          kind: "vertex",
          id: selectedSurface.id,
          ring: best.r - 1,
          index: best.index + 1,
        };
        canvasRef.current?.setPointerCapture?.(e.pointerId);
        return;
      }
      if (pointInPolygon(p, m.outer)) {
        setVsel(null);
        dragRef.current = {
          kind: "move",
          id: selectedSurface.id,
          start: p,
          quad: selectedSurface.quad,
          mask: m,
        };
        canvasRef.current?.setPointerCapture?.(e.pointerId);
        return;
      }
    }
    const h = hit(sp);
    if (!h) {
      props.onSelect(null, null);
      return;
    }
    props.onSelect(h.id, h.corner);
    const s = surfaces.find((x) => x.id === h.id);
    if (!s) return;
    dragRef.current =
      h.corner !== null
        ? { kind: "corner", id: h.id, index: h.corner }
        : { kind: "move", id: h.id, start: view.toProjector(sp), quad: s.quad, mask: s.mask };
    canvasRef.current?.setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e: RPointerEvent<HTMLCanvasElement>) => {
    const sp = toolPoint(e);
    const drag = dragRef.current;
    if (!drag) {
      if (adding || holeDraft) setCursor(clampPt(view.toProjector(sp), imgW, imgH));
      return;
    }
    if (drag.kind === "pan") {
      setPan({ x: drag.panX + sp[0] - drag.start[0], y: drag.panY + sp[1] - drag.start[1] });
      return;
    }
    const p = view.toProjector(sp);
    if (drag.kind === "mbox") {
      setDraftBox(boxFrom(drag.start, clampPt(p, imgW, imgH), 0));
      return;
    }
    if (drag.kind === "corner") {
      const s = surfaces.find((x) => x.id === drag.id);
      if (!s) return;
      const quad = s.quad.map((c) => [...c] as Pt) as Quad;
      quad[drag.index] = clampPt(p, imgW, imgH);
      props.onChangeQuad(drag.id, quad);
    } else if (drag.kind === "mesh") {
      const s = surfaces.find((x) => x.id === drag.id);
      if (!s?.mesh) return;
      props.onChangeMesh?.(drag.id, setControlPoint(s.mesh, s.quad, drag.i, drag.j, p));
    } else if (drag.kind === "vertex") {
      const m = surfaces.find((x) => x.id === drag.id)?.mask;
      if (!m) return;
      const v = clampPt(p, imgW, imgH);
      props.onChangeMask(drag.id, setVertex(m, drag.ring, drag.index, v));
    } else {
      moveSurface(drag.id, drag.quad, drag.mask, p[0] - drag.start[0], p[1] - drag.start[1]);
    }
  };

  /** Move quad and mask together by the shift the quad clamp allows. */
  const moveSurface = (id: string, quad: Quad, mask: Mask | null, dx: number, dy: number) => {
    const moved = moveQuadClamped(quad, dx, dy, imgW, imgH);
    if (!mask) {
      props.onChangeQuad(id, moved);
      return;
    }
    props.onChangeShape(
      id,
      moved,
      translateMask(mask, moved[0][0] - quad[0][0], moved[0][1] - quad[0][1]),
    );
  };

  const closeHole = (draft: Pt[]) => {
    const m = selectedSurface?.mask;
    setHoleDraft(null);
    if (m && selectedSurface) {
      props.onChangeMask(selectedSurface.id, addHole(m, draft));
      setVsel({ ring: m.holes.length, index: 0 });
    }
  };

  const endDrag = () => {
    dragRef.current = null;
  };

  const onPointerUp = (e: RPointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    dragRef.current = null;
    if (drag?.kind !== "mbox") return;
    setDraftBox(null);
    const end = clampPt(view.toProjector(toolPoint(e)), imgW, imgH);
    const bx = boxFrom(drag.start, end);
    if (bx) ms.setBox(bx);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLCanvasElement>) => {
    if (e.key === " ") {
      spaceRef.current = true;
      e.preventDefault();
      return;
    }
    if (e.key === "+" || e.key === "=") {
      e.preventDefault();
      zoomBy(ZOOM_STEP);
      return;
    }
    if (e.key === "-" || e.key === "_") {
      e.preventDefault();
      zoomBy(1 / ZOOM_STEP);
      return;
    }
    if (e.key === "0") {
      e.preventDefault();
      resetView();
      return;
    }
    if (ms.active) return;
    if (e.key === "Escape") {
      setAdding(null);
      setHoleDraft(null);
      return;
    }
    if (holeDraft) {
      if (e.key === "Enter" && holeDraft.length >= 3) {
        e.preventDefault();
        closeHole(holeDraft);
      }
      return;
    }
    const s = surfaces.find((x) => x.id === selectedId);
    if (!s) return;
    const editing = !!s.mask && editMask;
    if (s.mesh && meshMode && msel && e.key.startsWith("Arrow")) {
      e.preventDefault();
      const step = e.shiftKey ? 10 : 1;
      const dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0;
      const dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0;
      const cur = controlPoint(s, msel.i, msel.j);
      props.onChangeMesh?.(
        s.id,
        setControlPoint(s.mesh, s.quad, msel.i, msel.j, [cur[0] + dx, cur[1] + dy]),
      );
      return;
    }
    if (editing && s.mask && vsel && (e.key === "Delete" || e.key === "Backspace")) {
      e.preventDefault();
      const ring = vsel.ring < 0 ? s.mask.outer : s.mask.holes[vsel.ring];
      if (!ring || ring.length <= 3) {
        setNotice("A shape needs at least 3 points.");
        return;
      }
      setNotice("");
      props.onChangeMask(s.id, removeMaskVertex(s.mask, vsel.ring, vsel.index));
      setVsel(null);
    } else if (editing && s.mask && vsel && e.key.startsWith("Arrow")) {
      e.preventDefault();
      const step = e.shiftKey ? 10 : 1;
      const dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0;
      const dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0;
      const ring = vsel.ring < 0 ? s.mask.outer : s.mask.holes[vsel.ring];
      const cur = ring?.[vsel.index];
      if (cur) {
        props.onChangeMask(
          s.id,
          setVertex(s.mask, vsel.ring, vsel.index, clampPt([cur[0] + dx, cur[1] + dy], imgW, imgH)),
        );
      }
    } else if (e.key === "Delete") {
      e.preventDefault();
      props.onDelete(s.id);
    } else if (e.key === "Tab") {
      e.preventDefault();
      const n = e.shiftKey
        ? corner === null
          ? 3
          : (corner + 3) % 4
        : corner === null
          ? 0
          : (corner + 1) % 4;
      props.onSelect(s.id, n);
    } else if (e.key.startsWith("Arrow")) {
      e.preventDefault();
      const step = e.shiftKey ? 10 : 1;
      const dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0;
      const dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0;
      if (corner === null || editing) {
        moveSurface(s.id, s.quad, s.mask, dx, dy);
      } else {
        const quad = s.quad.map((c) => [...c] as Pt) as Quad;
        const cur = quad[corner] as Pt;
        quad[corner] = clampPt([cur[0] + dx, cur[1] + dy], imgW, imgH);
        props.onChangeQuad(s.id, quad);
      }
    }
  };

  const selected = selectedSurface;
  const hint = ms.active
    ? ms.boxMode
      ? "Drag a rectangle around the object."
      : "Click to include, right-click or Alt+click to exclude, or Add rectangle around it. Enter creates, Esc cancels."
    : holeDraft
      ? "Click the hole's points; click the first point or press Enter to close. Esc cancels."
      : notice
        ? notice
        : selected?.mask && !maskIsSimple(selected.mask)
          ? "The outline crosses itself. Drag its points apart until the lines no longer cross."
          : adding
            ? `Click the ${CORNER_NAMES[adding.length]} corner (${adding.length + 1} of 4). Esc cancels.`
            : selected && isSelfIntersecting(selected.quad)
              ? "Corners are crossed. Drag them so the four corners go around the shape in order."
              : selected?.mesh && meshMode
                ? msel && isMeshCorner(selected.mesh, msel)
                  ? "To move the whole corner, edit the surface corners instead."
                  : "Drag a grid point to bend the surface. Arrow keys nudge the selected point."
                : selected?.mask && editMask
                  ? "Drag the outline's points; click an edge to add a point."
                  : selected
                  ? "Drag the white corners onto the real corners. Arrow keys nudge, Tab picks a corner."
                  : surfaces.length > 0
                    ? "Click a surface to select it."
                    : "Start with Magic select, or Add rectangle.";
  const readout =
    selected?.mesh && meshMode && msel
      ? `Point ${msel.i},${msel.j}: ${controlPoint(selected, msel.i, msel.j)
          .map((v) => Math.round(v))
          .join(", ")} px`
      : selected && corner !== null
        ? `Corner ${CORNER_NAMES[corner]}: ${Math.round((selected.quad[corner] as Pt)[0])}, ${Math.round((selected.quad[corner] as Pt)[1])} px`
        : "";

  return (
    <div className={styles.root}>
      <div className={styles.toolbar}>
        {props.magic && <MagicControls ms={ms} />}
        {props.magic && !ms.active ? (
          <InfoTip term="Magic select">
            Click once on a door, window or panel in the picture and FacadeKit finds its outline
            for you. Click more spots to add to it, right-click to leave a spot out, then press
            Create surface.
          </InfoTip>
        ) : null}
        <button
          type="button"
          onClick={() => setAdding([])}
          disabled={adding !== null}
          title="Click the four corners of a door or window on the picture, one after another"
        >
          Click 4 corners
        </button>
        <button
          type="button"
          onClick={() =>
            ms.active ? ms.toggleBoxMode() : props.onCreate(rectQuad(imgW, imgH, 0.4))
          }
          aria-pressed={ms.active ? ms.boxMode : undefined}
          title={
            ms.active
              ? "Drag a rectangle around the object; the selector looks inside it"
              : "Put a rectangle in the middle of the picture, then drag its corners into place"
          }
        >
          Add rectangle
        </button>
        {selected?.mask && (
          <>
            <button type="button" onClick={() => setHoleDraft([])} disabled={holeDraft !== null}>
              Add hole
            </button>
            <button
              type="button"
              onClick={() => {
                setEditMask((v) => !v);
                setEditMesh(false);
              }}
              aria-pressed={editMask}
            >
              {editMask ? "Edit corners" : "Edit outline"}
            </button>
          </>
        )}
        {selected?.mesh && (
          <button
            type="button"
            onClick={() => {
              setEditMesh((v) => !v);
              setEditMask(false);
            }}
            aria-pressed={meshMode}
          >
            {meshMode ? "Done bending" : "Edit bend"}
          </button>
        )}
        <span className={styles.hint} role="status" id="surface-canvas-hint">
          {hint}
        </span>
        <span className={styles.readout}>{readout}</span>
      </div>
      <div className={styles.wrap} ref={wrapRef}>
        <div className={styles.zoom} role="group" aria-label="View">
          <button
            type="button"
            onClick={() => zoomBy(1 / ZOOM_STEP)}
            disabled={zoom <= MIN_ZOOM}
            aria-label="Zoom out"
            title="Zoom out (−)"
          >
            −
          </button>
          <span className={styles.zoomValue} aria-live="off">
            {Math.round(zoom * 100)} %
          </span>
          <button
            type="button"
            onClick={() => zoomBy(ZOOM_STEP)}
            disabled={zoom >= MAX_ZOOM}
            aria-label="Zoom in"
            title="Zoom in (+)"
          >
            +
          </button>
          <button
            type="button"
            onClick={resetView}
            disabled={zoom === MIN_ZOOM && pan.x === 0 && pan.y === 0}
            title="Show the whole picture (0)"
          >
            Reset view
          </button>
        </div>
        {showTip && surfaces.length > 0 && !ms.active && !adding ? (
          <div className={styles.tip} role="note" aria-label="Tip">
            <p>
              <strong>Tip:</strong> drag the white corner squares onto the real corners of the
              door or window. Drag inside a surface to move it. Scroll or use + and − to zoom in
              for a precise fit.
            </p>
            <button type="button" className="primary" onClick={dismissTip}>
              Got it
            </button>
          </div>
        ) : null}
        <canvas
          ref={canvasRef}
          className={styles.canvas}
          aria-label="Surface canvas"
          aria-describedby="surface-canvas-hint"
          aria-keyshortcuts="ArrowUp ArrowDown ArrowLeft ArrowRight Tab Delete + - 0"
          tabIndex={0}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={endDrag}
          onKeyDown={onKeyDown}
          onKeyUp={(e) => {
            if (e.key === " ") spaceRef.current = false;
          }}
          onBlur={() => {
            spaceRef.current = false;
          }}
          onContextMenu={(e) => e.preventDefault()}
        />
      </div>
    </div>
  );
}
