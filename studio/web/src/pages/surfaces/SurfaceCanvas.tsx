import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as RPointerEvent } from "react";
import type { Pt, Surface } from "../../api/types";
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
import styles from "./SurfaceCanvas.module.css";

const CORNER_NAMES = ["top-left", "top-right", "bottom-right", "bottom-left"];
const HANDLE = 8;
const HIT_RADIUS = 10;
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
  onCreate: (quad: Quad) => void;
  onDelete: (id: string) => void;
}

type Drag =
  | { kind: "corner"; id: string; index: number }
  | { kind: "move"; id: string; start: Pt; quad: Quad }
  | { kind: "pan"; start: Pt; panX: number; panY: number };

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
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      setZoom((z) => {
        const nz = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
        if (nz === z) return z;
        const cur = viewRef.current;
        const anchor = cur.toProjector([sx, sy]);
        const base = makeView({
          imgW,
          imgH,
          viewW: size.w,
          viewH: size.h,
          zoom: nz,
          panX: 0,
          panY: 0,
        });
        const at = base.toScreen(anchor);
        setPan(nz === MIN_ZOOM ? { x: 0, y: 0 } : { x: sx - at[0], y: sy - at[1] });
        return nz;
      });
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [imgW, imgH, size.w, size.h]);

  // Esc cancels adding, even when focus left the canvas.
  useEffect(() => {
    if (!adding) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAdding(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [adding]);

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
      path(s.quad, true);
      if (selected) {
        ctx.fillStyle = "rgba(80,160,255,0.25)";
        ctx.fill();
      }
      stroke(crossed ? "#ff3030" : "#ffffff");
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
    if (adding && adding.length > 0) {
      const pts = cursor ? [...adding, cursor] : adding;
      path(pts, false);
      stroke("#ffcc00");
    }
  }, [view, size, surfaces, selectedId, corner, adding, cursor, loaded, imgW, imgH]);

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
        : { kind: "move", id: h.id, start: view.toProjector(sp), quad: s.quad };
    canvasRef.current?.setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e: RPointerEvent<HTMLCanvasElement>) => {
    const sp = toolPoint(e);
    const drag = dragRef.current;
    if (!drag) {
      if (adding) setCursor(clampPt(view.toProjector(sp), imgW, imgH));
      return;
    }
    if (drag.kind === "pan") {
      setPan({ x: drag.panX + sp[0] - drag.start[0], y: drag.panY + sp[1] - drag.start[1] });
      return;
    }
    const p = view.toProjector(sp);
    if (drag.kind === "corner") {
      const s = surfaces.find((x) => x.id === drag.id);
      if (!s) return;
      const quad = s.quad.map((c) => [...c] as Pt) as Quad;
      quad[drag.index] = clampPt(p, imgW, imgH);
      props.onChangeQuad(drag.id, quad);
    } else {
      props.onChangeQuad(
        drag.id,
        moveQuadClamped(drag.quad, p[0] - drag.start[0], p[1] - drag.start[1], imgW, imgH),
      );
    }
  };

  const endDrag = () => {
    dragRef.current = null;
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLCanvasElement>) => {
    if (e.key === " ") {
      spaceRef.current = true;
      e.preventDefault();
      return;
    }
    if (e.key === "Escape") {
      setAdding(null);
      return;
    }
    const s = surfaces.find((x) => x.id === selectedId);
    if (!s) return;
    if (e.key === "Delete") {
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
      if (corner === null) {
        props.onChangeQuad(s.id, moveQuadClamped(s.quad, dx, dy, imgW, imgH));
      } else {
        const quad = s.quad.map((c) => [...c] as Pt) as Quad;
        const cur = quad[corner] as Pt;
        quad[corner] = clampPt([cur[0] + dx, cur[1] + dy], imgW, imgH);
        props.onChangeQuad(s.id, quad);
      }
    }
  };

  const selected = surfaces.find((s) => s.id === selectedId);
  const hint = adding
    ? `Click the ${CORNER_NAMES[adding.length]} corner (${adding.length + 1} of 4). Esc cancels.`
    : selected && isSelfIntersecting(selected.quad)
      ? "Corners are crossed"
      : "Scroll to zoom, Space or middle button to pan.";
  const readout =
    selected && corner !== null
      ? `Corner ${CORNER_NAMES[corner]}: ${Math.round((selected.quad[corner] as Pt)[0])}, ${Math.round((selected.quad[corner] as Pt)[1])} px`
      : "";

  return (
    <div className={styles.root}>
      <div className={styles.toolbar}>
        <button type="button" onClick={() => setAdding([])} disabled={adding !== null}>
          Add surface
        </button>
        <button type="button" onClick={() => props.onCreate(rectQuad(imgW, imgH, 0.4))}>
          Add rectangle
        </button>
        <span className={styles.hint} role="status">
          {hint}
        </span>
        <span className={styles.readout}>{readout}</span>
      </div>
      <div className={styles.wrap} ref={wrapRef}>
        <canvas
          ref={canvasRef}
          className={styles.canvas}
          aria-label="Surface canvas"
          tabIndex={0}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
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
