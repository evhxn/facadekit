import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import type { CameraRoi } from "../../api/types";
import styles from "../SetupPage.module.css";
import { COPY } from "./cameraCopy";

const MIN_SIDE = 64;

export interface Frame {
  width: number;
  height: number;
}

/** A box in camera pixels: [x0, x1) by [y0, y1). */
interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** Which edges a handle moves. */
interface Sides {
  left?: boolean;
  right?: boolean;
  top?: boolean;
  bottom?: boolean;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const floorEven = (v: number) => 2 * Math.floor(v / 2);
const ceilEven = (v: number) => 2 * Math.ceil(v / 2);

const toBox = (r: CameraRoi): Box => ({ x0: r.x, y0: r.y, x1: r.x + r.w, y1: r.y + r.h });

/**
 * Moves the chosen edges of `start` by (dx, dy) camera pixels. The moved edges are rounded
 * outwards to even numbers and clamped to the frame; the box never gets smaller than 64 x 64.
 * These are the same rules the backend checks again.
 */
export function moveEdges(start: Box, sides: Sides, dx: number, dy: number, frame: Frame): Box {
  const b = { ...start };
  if (sides.left) b.x0 = floorEven(clamp(start.x0 + dx, 0, start.x1 - MIN_SIDE));
  if (sides.right) b.x1 = Math.min(frame.width, ceilEven(clamp(start.x1 + dx, start.x0 + MIN_SIDE, frame.width)));
  if (sides.top) b.y0 = floorEven(clamp(start.y0 + dy, 0, start.y1 - MIN_SIDE));
  if (sides.bottom) b.y1 = Math.min(frame.height, ceilEven(clamp(start.y1 + dy, start.y0 + MIN_SIDE, frame.height)));
  return b;
}

const CORNERS: { id: keyof typeof COPY.corners; sides: Sides }[] = [
  { id: "tl", sides: { left: true, top: true } },
  { id: "tr", sides: { right: true, top: true } },
  { id: "bl", sides: { left: true, bottom: true } },
  { id: "br", sides: { right: true, bottom: true } },
];
const EDGES: { id: string; sides: Sides }[] = [
  { id: "top", sides: { top: true } },
  { id: "bottom", sides: { bottom: true } },
  { id: "left", sides: { left: true } },
  { id: "right", sides: { right: true } },
];

interface Props {
  /** Size of the camera picture in pixels (the live view image's natural size). */
  frame: Frame;
  /** Box shown when the editor opens. */
  initial: CameraRoi;
  /** Where Reset goes back to. */
  suggested: CameraRoi | null;
  /** Show the box and its buttons; when false only the children are drawn. */
  active: boolean;
  onUse: (roi: CameraRoi) => void;
  onWhole: () => void;
  /** The live view image. The box is laid over it. */
  children: ReactNode;
}

export default function CropBox({ frame, initial, suggested, active, onUse, onWhole, children }: Props) {
  const area = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState<Box>(() => toBox(initial));
  const [edited, setEdited] = useState(false);
  const [base, setBase] = useState(initial); // returned as it is when the box was not moved
  const cleanup = useRef<(() => void) | null>(null);

  useEffect(() => {
    setBox(toBox(initial));
    setBase(initial);
    setEdited(false);
  }, [initial, active]);

  useEffect(() => () => cleanup.current?.(), []);

  const roiOf = (b: Box): CameraRoi => ({
    x: b.x0,
    y: b.y0,
    w: b.x1 - b.x0,
    h: b.y1 - b.y0,
    frame_width: frame.width,
    frame_height: frame.height,
    bin: 1,
  });

  const change = (next: Box) => {
    setBox(next);
    setEdited(true);
  };

  const onPointerDown = (sides: Sides) => (e: PointerEvent<HTMLElement>) => {
    const rect = area.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return;
    e.preventDefault();
    // Display pixels -> camera pixels: the picture's own size against the size on screen.
    const kx = frame.width / rect.width;
    const ky = frame.height / rect.height;
    cleanup.current?.();
    const state = { sides, x: e.clientX, y: e.clientY, start: box };
    const move = (ev: globalThis.PointerEvent | MouseEvent) => {
      change(moveEdges(state.start, state.sides, (ev.clientX - state.x) * kx, (ev.clientY - state.y) * ky, frame));
    };
    const stop = () => cleanup.current?.();
    cleanup.current = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      cleanup.current = null;
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
  };

  const onKeyDown = (sides: Sides) => (e: KeyboardEvent<HTMLElement>) => {
    const dirs: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    };
    const dir = dirs[e.key];
    if (!dir) return;
    e.preventDefault();
    const pct = e.shiftKey ? 0.05 : 0.01;
    const sx = Math.max(2, frame.width * pct);
    const sy = Math.max(2, frame.height * pct);
    change(moveEdges(box, sides, dir[0] * sx, dir[1] * sy, frame));
  };

  const pos = (x: number, y: number) => ({
    left: `${(x / frame.width) * 100}%`,
    top: `${(y / frame.height) * 100}%`,
  });

  const roi = roiOf(box);
  return (
    <div>
      <div className={styles.cropArea} ref={area} data-testid="crop-area">
        {children}
        {active ? (
          <div
            className={styles.cropBox}
            data-testid="crop-box"
            style={{
              ...pos(box.x0, box.y0),
              width: `${(roi.w / frame.width) * 100}%`,
              height: `${(roi.h / frame.height) * 100}%`,
            }}
          >
            {EDGES.map((edge) => (
              <div
                key={edge.id}
                className={`${styles.cropEdge} ${styles[`edge_${edge.id}`] ?? ""}`}
                data-testid={`crop-edge-${edge.id}`}
                aria-hidden="true"
                onPointerDown={onPointerDown(edge.sides)}
              />
            ))}
            {CORNERS.map((c) => (
              <button
                key={c.id}
                type="button"
                className={`${styles.cropHandle} ${styles[`handle_${c.id}`] ?? ""}`}
                aria-label={COPY.handleLabel(COPY.corners[c.id])}
                onPointerDown={onPointerDown(c.sides)}
                onKeyDown={onKeyDown(c.sides)}
              />
            ))}
          </div>
        ) : null}
      </div>
      {active ? (
        <>
          <p className={styles.note}>{COPY.cropCaption}</p>
          <p className={styles.note} role="status">
            {COPY.cropSize(roi.w, roi.h)}
          </p>
          <div className={styles.row}>
            <button type="button" className="primary" onClick={() => onUse(edited ? roi : base)}>
              {COPY.cropUse}
            </button>
            <button
              type="button"
              onClick={() => {
                const target = suggested ?? initial;
                setBox(toBox(target));
                setBase(target);
                setEdited(false);
              }}
            >
              {COPY.cropReset}
            </button>
            <button type="button" onClick={onWhole}>
              {COPY.cropWhole}
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}
