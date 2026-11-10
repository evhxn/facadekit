import { describe, expect, it } from "vitest";
import type { Mesh, Surface } from "../../api/types";
import {
  controlPoint,
  gridPolylines,
  identityMesh,
  interpolateOffset,
  resampleMesh,
  setControlPoint,
} from "./meshMath";

const quad: Surface["quad"] = [
  [100, 50],
  [420, 70],
  [400, 250],
  [90, 220],
];
const surf = (mesh: Mesh | null) => ({ quad, mesh });

describe("meshMath", () => {
  it("identityMesh(2, 2) has 9 zero offsets", () => {
    const m = identityMesh(2, 2);
    expect(m.offsets).toHaveLength(9);
    expect(m.offsets.every((o) => o[0] === 0 && o[1] === 0)).toBe(true);
  });

  it("controlPoint of a zero mesh is the homography image; corners are the quad corners", () => {
    const s = surf(identityMesh(3, 2));
    expect(controlPoint(s, 0, 0)[0]).toBeCloseTo(100, 6);
    expect(controlPoint(s, 0, 3)[1]).toBeCloseTo(70, 6);
    expect(controlPoint(s, 2, 3)[0]).toBeCloseTo(400, 6);
    expect(controlPoint(s, 2, 0)[1]).toBeCloseTo(220, 6);
  });

  it("setControlPoint then controlPoint round-trips and only changes that point", () => {
    const mesh = setControlPoint(identityMesh(4, 4), quad, 2, 3, [301.5, 123.25]);
    const p = controlPoint(surf(mesh), 2, 3);
    expect(p[0]).toBeCloseTo(301.5, 9);
    expect(p[1]).toBeCloseTo(123.25, 9);
    mesh.offsets.forEach((o, k) => {
      if (k !== 2 * 5 + 3) expect(o).toEqual([0, 0]);
    });
  });

  it("interpolateOffset hits control points and averages between them", () => {
    const m = identityMesh(2, 2);
    m.offsets[4] = [10, -20];
    m.offsets[5] = [30, 0];
    expect(interpolateOffset(m, 0.5, 0.5)).toEqual([10, -20]);
    expect(interpolateOffset(m, 1, 0.5)).toEqual([30, 0]);
    const mid = interpolateOffset(m, 0.75, 0.5);
    expect(mid[0]).toBeCloseTo(20, 9);
    expect(mid[1]).toBeCloseTo(-10, 9);
    expect(interpolateOffset(m, 0, 0)).toEqual([0, 0]);
  });

  it("resampleMesh 2x2 -> 4x4 preserves offsets at coincident points", () => {
    const m = identityMesh(2, 2);
    m.offsets[4] = [12, -8];
    m.offsets[0] = [3, 4];
    const r = resampleMesh(m, 4, 4);
    expect(r.cols).toBe(4);
    expect(r.offsets).toHaveLength(25);
    expect(r.offsets[2 * 5 + 2]).toEqual([12, -8]);
    expect(r.offsets[0]).toEqual([3, 4]);
    expect(r.offsets[4 * 5 + 4]).toEqual([0, 0]);
    const q = r.offsets[1 * 5 + 2]!; // halfway between (0,.5) and (.5,.5) in v
    expect(q[0]).toBeCloseTo(6, 9);
  });

  it("gridPolylines gives rows+1 horizontal and cols+1 vertical lines", () => {
    const lines = gridPolylines(surf(identityMesh(3, 2)));
    expect(lines).toHaveLength(3 + 4);
    expect(lines[0]).toHaveLength(4);
    expect(lines[3]).toHaveLength(3);
    expect(gridPolylines(surf(null))).toEqual([]);
  });
});
