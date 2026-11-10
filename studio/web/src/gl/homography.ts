export type Pt = [number, number];
/** Row-major 3x3 matrix. */
export type Mat3 = [number, number, number, number, number, number, number, number, number];

export const IDENTITY: Mat3 = [1, 0, 0, 0, 1, 0, 0, 0, 1];

const EPS = 1e-12;

/** Solve A x = b (n x n) by Gaussian elimination with partial pivoting; null if singular. */
function solve(a: number[][], b: number[]): number[] | null {
  const n = b.length;
  const m = a.map((row, i) => [...row, b[i]!]);
  for (let col = 0; col < n; col++) {
    let piv = col;
    for (let r = col + 1; r < n; r++) {
      if (Math.abs(m[r]![col]!) > Math.abs(m[piv]![col]!)) piv = r;
    }
    if (Math.abs(m[piv]![col]!) < EPS) return null;
    [m[col], m[piv]] = [m[piv]!, m[col]!];
    for (let r = col + 1; r < n; r++) {
      const f = m[r]![col]! / m[col]![col]!;
      for (let c = col; c <= n; c++) m[r]![c]! -= f * m[col]![c]!;
    }
  }
  const x = new Array<number>(n).fill(0);
  for (let r = n - 1; r >= 0; r--) {
    let s = m[r]![n]!;
    for (let c = r + 1; c < n; c++) s -= m[r]![c]! * x[c]!;
    x[r] = s / m[r]![r]!;
  }
  return x;
}

/** Like quadHomography but returns null for degenerate quads. */
export function tryQuadHomography(srcW: number, srcH: number, quad: Pt[]): Mat3 | null {
  if (quad.length < 4) return null;
  const src: Pt[] = [
    [0, 0],
    [srcW, 0],
    [srcW, srcH],
    [0, srcH],
  ];
  const a: number[][] = [];
  const b: number[] = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = src[i]!;
    const [u, v] = quad[i]!;
    a.push([x, y, 1, 0, 0, 0, -u * x, -u * y]);
    b.push(u);
    a.push([0, 0, 0, x, y, 1, -v * x, -v * y]);
    b.push(v);
  }
  const h = solve(a, b);
  if (!h || h.some((v) => !Number.isFinite(v))) return null;
  return [h[0]!, h[1]!, h[2]!, h[3]!, h[4]!, h[5]!, h[6]!, h[7]!, 1];
}

/** Maps (0,0),(srcW,0),(srcW,srcH),(0,srcH) to quad[0..3]. Degenerate quads give the identity. */
export function quadHomography(srcW: number, srcH: number, quad: Pt[]): Mat3 {
  return tryQuadHomography(srcW, srcH, quad) ?? [...IDENTITY];
}

export function applyH(h: Mat3, p: Pt): Pt {
  const [x, y] = p;
  const w = h[6] * x + h[7] * y + h[8];
  return [(h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w];
}

export function multiply(a: Mat3, b: Mat3): Mat3 {
  const o = new Array<number>(9).fill(0);
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      let s = 0;
      for (let k = 0; k < 3; k++) s += a[r * 3 + k]! * b[k * 3 + c]!;
      o[r * 3 + c] = s;
    }
  }
  return o as Mat3;
}

/** Matrix inverse via the adjugate; a singular matrix gives the identity. */
export function invert(h: Mat3): Mat3 {
  const [a, b, c, d, e, f, g, i, j] = h;
  const A = e * j - f * i;
  const B = -(d * j - f * g);
  const C = d * i - e * g;
  const det = a * A + b * B + c * C;
  if (!Number.isFinite(det) || Math.abs(det) < EPS) return [...IDENTITY];
  const s = 1 / det;
  return [
    A * s,
    -(b * j - c * i) * s,
    (b * f - c * e) * s,
    B * s,
    (a * j - c * g) * s,
    -(a * f - c * d) * s,
    C * s,
    -(a * i - b * g) * s,
    (a * e - b * d) * s,
  ];
}

const dist = (p: Pt, q: Pt) => Math.hypot(p[0] - q[0], p[1] - q[1]);

/** Width / height from the mean edge lengths of the quad (tl, tr, br, bl). */
export function estimateAspect(quad: Pt[]): number {
  const [tl, tr, br, bl] = quad as [Pt, Pt, Pt, Pt];
  const w = (dist(tl, tr) + dist(bl, br)) / 2;
  const h = (dist(tl, bl) + dist(tr, br)) / 2;
  return h > 0 ? w / h : 1;
}

export type Rect = [number, number, number, number];

export function fitRects(
  srcW: number,
  srcH: number,
  surfaceAspect: number,
  fit: "fill" | "fit" | "stretch",
): { src: Rect; dst: Rect } {
  const sa = srcW / srcH;
  const ta = surfaceAspect;
  if (fit === "stretch") return { src: [0, 0, srcW, srcH], dst: [0, 0, 1, 1] };
  if (fit === "fill") {
    if (sa > ta) {
      const w = srcH * ta;
      const x0 = (srcW - w) / 2;
      return { src: [x0, 0, x0 + w, srcH], dst: [0, 0, 1, 1] };
    }
    const h = srcW / ta;
    const y0 = (srcH - h) / 2;
    return { src: [0, y0, srcW, y0 + h], dst: [0, 0, 1, 1] };
  }
  if (sa > ta) {
    const h = ta / sa;
    const y0 = (1 - h) / 2;
    return { src: [0, 0, srcW, srcH], dst: [0, y0, 1, y0 + h] };
  }
  const w = sa / ta;
  const x0 = (1 - w) / 2;
  return { src: [0, 0, srcW, srcH], dst: [x0, 0, x0 + w, 1] };
}
