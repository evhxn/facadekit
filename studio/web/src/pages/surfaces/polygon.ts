import type { Pt } from "../../api/types";

/** Minimum number of vertices in an outline or a hole. */
export const MIN_VERTICES = 3;

/** Insert `pt` after vertex `edgeIndex` (edge i runs from vertex i to vertex i+1, wrapping). */
export function insertVertex(poly: Pt[], edgeIndex: number, pt: Pt): Pt[] {
  const out = poly.map((p) => [p[0], p[1]] as Pt);
  out.splice(edgeIndex + 1, 0, [pt[0], pt[1]]);
  return out;
}

/** Remove vertex i. A polygon never drops below 3 vertices: then the input is returned as is. */
export function removeVertex(poly: Pt[], i: number): Pt[] {
  if (poly.length <= MIN_VERTICES || i < 0 || i >= poly.length) return poly;
  return poly.filter((_, k) => k !== i).map((p) => [p[0], p[1]] as Pt);
}

export interface NearestEdge {
  /** Edge i runs from vertex i to vertex (i + 1) % n. */
  index: number;
  distance: number;
  /** Closest point on that edge. */
  point: Pt;
}

/** Closest edge of a closed polygon to `pt`. */
export function nearestEdge(poly: Pt[], pt: Pt): NearestEdge {
  let best: NearestEdge = { index: -1, distance: Infinity, point: [0, 0] };
  const n = poly.length;
  for (let i = 0; i < n; i++) {
    const a = poly[i] as Pt;
    const b = poly[(i + 1) % n] as Pt;
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const len2 = dx * dx + dy * dy;
    const t =
      len2 === 0 ? 0 : Math.max(0, Math.min(1, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / len2));
    const q: Pt = [a[0] + t * dx, a[1] + t * dy];
    const d = Math.hypot(pt[0] - q[0], pt[1] - q[1]);
    if (d < best.distance) best = { index: i, distance: d, point: q };
  }
  return best;
}

/** Even-odd point-in-polygon test. */
export function pointInPolygon(pt: Pt, poly: Pt[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i] as Pt;
    const [xj, yj] = poly[j] as Pt;
    if (yi > pt[1] !== yj > pt[1] && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

export function translate(poly: Pt[], dx: number, dy: number): Pt[] {
  return poly.map(([x, y]) => [x + dx, y + dy] as Pt);
}

function orient(o: Pt, a: Pt, b: Pt): number {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
}

function properCross(a: Pt, b: Pt, c: Pt, d: Pt): boolean {
  return orient(a, b, c) * orient(a, b, d) < 0 && orient(c, d, a) * orient(c, d, b) < 0;
}

/** True when no two non-adjacent edges cross. */
export function isSimple(poly: Pt[]): boolean {
  const n = poly.length;
  if (n < MIN_VERTICES) return false;
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (j === i + 1 || (i === 0 && j === n - 1)) continue; // adjacent edges share a vertex
      if (
        properCross(poly[i] as Pt, poly[(i + 1) % n] as Pt, poly[j] as Pt, poly[(j + 1) % n] as Pt)
      ) {
        return false;
      }
    }
  }
  return true;
}
