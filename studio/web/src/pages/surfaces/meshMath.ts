import type { Mesh, Pt, Surface } from "../../api/types";
import { applyH, quadHomography } from "../../gl/homography";

export const MESH_SIZES = [2, 3, 4, 6, 8] as const;

/** All-zero mesh of `cols` x `rows` cells. */
export function identityMesh(cols: number, rows: number): Mesh {
  return {
    cols,
    rows,
    offsets: Array.from({ length: (cols + 1) * (rows + 1) }, () => [0, 0] as Pt),
  };
}

export function isZeroMesh(mesh: Mesh | null | undefined): boolean {
  return !mesh || mesh.offsets.every((o) => o[0] === 0 && o[1] === 0);
}

/** Base (pre-offset) position of control point (i = row, j = column). */
export function basePoint(quad: Surface["quad"], mesh: Mesh, i: number, j: number): Pt {
  return applyH(quadHomography(1, 1, quad), [j / mesh.cols, i / mesh.rows]);
}

/** Final projector position of control point (i = row, j = column). */
export function controlPoint(surface: Pick<Surface, "quad" | "mesh">, i: number, j: number): Pt {
  const mesh = surface.mesh;
  if (!mesh) throw new Error("surface has no mesh");
  const b = basePoint(surface.quad, mesh, i, j);
  const o = mesh.offsets[i * (mesh.cols + 1) + j]!;
  return [b[0] + o[0], b[1] + o[1]];
}

/** Moves control point (i, j) to `finalPos` by storing `finalPos - base` as its offset. */
export function setControlPoint(
  mesh: Mesh,
  quad: Surface["quad"],
  i: number,
  j: number,
  finalPos: Pt,
): Mesh {
  const b = basePoint(quad, mesh, i, j);
  const k = i * (mesh.cols + 1) + j;
  return {
    ...mesh,
    offsets: mesh.offsets.map((o, n) =>
      n === k ? ([finalPos[0] - b[0], finalPos[1] - b[1]] as Pt) : ([o[0], o[1]] as Pt),
    ),
  };
}

/** Bilinear offset at unit coordinates (u, v), clamped to the surface (matches the backend). */
export function interpolateOffset(mesh: Mesh, u: number, v: number): Pt {
  const { cols, rows, offsets } = mesh;
  const fj = Math.min(cols, Math.max(0, u * cols));
  const fi = Math.min(rows, Math.max(0, v * rows));
  const j0 = Math.min(Math.floor(fj), cols - 1);
  const i0 = Math.min(Math.floor(fi), rows - 1);
  const tj = fj - j0;
  const ti = fi - i0;
  const at = (i: number, j: number) => offsets[i * (cols + 1) + j]!;
  const a = at(i0, j0);
  const b = at(i0, j0 + 1);
  const c = at(i0 + 1, j0);
  const d = at(i0 + 1, j0 + 1);
  const w00 = (1 - tj) * (1 - ti);
  const w01 = tj * (1 - ti);
  const w10 = (1 - tj) * ti;
  const w11 = tj * ti;
  return [
    a[0] * w00 + b[0] * w01 + c[0] * w10 + d[0] * w11,
    a[1] * w00 + b[1] * w01 + c[1] * w10 + d[1] * w11,
  ];
}

/** Samples the existing offsets onto a new grid so a size change keeps the bend. */
export function resampleMesh(mesh: Mesh, newCols: number, newRows: number): Mesh {
  const out = identityMesh(newCols, newRows);
  for (let i = 0; i <= newRows; i++) {
    for (let j = 0; j <= newCols; j++) {
      out.offsets[i * (newCols + 1) + j] = interpolateOffset(mesh, j / newCols, i / newRows);
    }
  }
  return out;
}

/** Grid lines through the final control points: rows+1 horizontal, then cols+1 vertical. */
export function gridPolylines(surface: Pick<Surface, "quad" | "mesh">): Pt[][] {
  const mesh = surface.mesh;
  if (!mesh) return [];
  const lines: Pt[][] = [];
  for (let i = 0; i <= mesh.rows; i++) {
    lines.push(Array.from({ length: mesh.cols + 1 }, (_, j) => controlPoint(surface, i, j)));
  }
  for (let j = 0; j <= mesh.cols; j++) {
    lines.push(Array.from({ length: mesh.rows + 1 }, (_, i) => controlPoint(surface, i, j)));
  }
  return lines;
}
