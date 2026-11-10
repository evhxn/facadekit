import type { Pt } from "../../api/types";

export type Quad = [Pt, Pt, Pt, Pt];

const dist = (a: Pt, b: Pt) => Math.hypot(a[0] - b[0], a[1] - b[1]);

/** Real-world width / height estimate. Must match the backend's estimate_aspect. */
export function estimateAspect(quad: Quad): number {
  const [tl, tr, br, bl] = quad;
  const w = dist(tl, tr) + dist(bl, br);
  const h = dist(tl, bl) + dist(tr, br);
  return h === 0 ? 1 : w / h;
}

function cross(o: Pt, a: Pt, b: Pt): number {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
}

/** Point in a simple polygon given by the quad's corners (even-odd rule). */
export function pointInQuad(pt: Pt, quad: Quad): boolean {
  let inside = false;
  for (let i = 0, j = 3; i < 4; j = i++) {
    const [xi, yi] = quad[i] as Pt;
    const [xj, yj] = quad[j] as Pt;
    if (yi > pt[1] !== yj > pt[1] && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

/** Index of the corner within `radius` of `pt` (nearest wins), or -1. */
export function hitTestCorner(pt: Pt, quad: Quad, radius: number): number {
  let best = -1;
  let bestD = radius;
  quad.forEach((c, i) => {
    const d = dist(pt, c);
    if (d <= bestD) {
      bestD = d;
      best = i;
    }
  });
  return best;
}

export function moveQuad(quad: Quad, dx: number, dy: number): Quad {
  return quad.map(([x, y]) => [x + dx, y + dy] as Pt) as Quad;
}

export const CLAMP_MARGIN = 200;

export function clampPt(pt: Pt, w: number, h: number): Pt {
  return [
    Math.min(w + CLAMP_MARGIN, Math.max(-CLAMP_MARGIN, pt[0])),
    Math.min(h + CLAMP_MARGIN, Math.max(-CLAMP_MARGIN, pt[1])),
  ];
}

/** Move a whole quad, limiting the shift so no corner leaves the allowed area. */
export function moveQuadClamped(quad: Quad, dx: number, dy: number, w: number, h: number): Quad {
  const xs = quad.map((p) => p[0]);
  const ys = quad.map((p) => p[1]);
  const cx = Math.min(
    Math.max(dx, -CLAMP_MARGIN - Math.min(...xs)),
    w + CLAMP_MARGIN - Math.max(...xs),
  );
  const cy = Math.min(
    Math.max(dy, -CLAMP_MARGIN - Math.min(...ys)),
    h + CLAMP_MARGIN - Math.max(...ys),
  );
  return moveQuad(quad, cx, cy);
}

/** Swap TL<->TR and BL<->BR. */
export function flipHorizontal(quad: Quad): Quad {
  const [tl, tr, br, bl] = quad;
  return [[...tr], [...tl], [...bl], [...br]];
}

/** Cycle the corner order by one: the old TR becomes TL, and so on. */
export function rotateCorners(quad: Quad): Quad {
  const [tl, tr, br, bl] = quad;
  return [[...tr], [...br], [...bl], [...tl]];
}

function segmentsCross(a: Pt, b: Pt, c: Pt, d: Pt): boolean {
  return cross(a, b, c) * cross(a, b, d) < 0 && cross(c, d, a) * cross(c, d, b) < 0;
}

/** True for a bow-tie: opposite edges cross. */
export function isSelfIntersecting(quad: Quad): boolean {
  const [a, b, c, d] = quad;
  return segmentsCross(a, b, c, d) || segmentsCross(b, c, d, a);
}

export function rectQuad(w: number, h: number, areaFraction: number): Quad {
  const s = Math.sqrt(areaFraction);
  const rw = w * s;
  const rh = h * s;
  const x0 = (w - rw) / 2;
  const y0 = (h - rh) / 2;
  return [
    [x0, y0],
    [x0 + rw, y0],
    [x0 + rw, y0 + rh],
    [x0, y0 + rh],
  ];
}

export interface ViewParams {
  imgW: number;
  imgH: number;
  viewW: number;
  viewH: number;
  zoom: number;
  panX: number;
  panY: number;
}

export interface View {
  scale: number;
  toScreen: (pt: Pt) => Pt;
  toProjector: (pt: Pt) => Pt;
}

/** Image fitted (letterboxed) in the view, then zoomed and panned. */
export function makeView(p: ViewParams): View {
  const scale = Math.min(p.viewW / p.imgW, p.viewH / p.imgH) * p.zoom;
  const ox = (p.viewW - p.imgW * scale) / 2 + p.panX;
  const oy = (p.viewH - p.imgH * scale) / 2 + p.panY;
  return {
    scale,
    toScreen: ([x, y]) => [ox + x * scale, oy + y * scale],
    toProjector: ([x, y]) => [(x - ox) / scale, (y - oy) / scale],
  };
}
