import { describe, expect, it } from "vitest";
import type { Surface } from "../api/types";
import { identityMesh, setControlPoint } from "../pages/surfaces/meshMath";
import { buildMeshVertices } from "./meshGeometry";
import { applyH, quadHomography } from "./homography";

const quad: Surface["quad"] = [
  [100, 50],
  [420, 70],
  [400, 250],
  [90, 220],
];

describe("buildMeshVertices", () => {
  it("zero mesh: positions equal the homography image of every (u, v)", () => {
    const mesh = identityMesh(3, 2);
    const v = buildMeshVertices({ quad, mesh }, 4);
    const nc = 3 * 4 + 1;
    const nr = 2 * 4 + 1;
    expect(v.positions.length).toBe(nc * nr * 2);
    expect(v.uvw.length).toBe(nc * nr * 3);
    const h = quadHomography(1, 1, quad);
    for (let i = 0; i < nr; i++) {
      for (let j = 0; j < nc; j++) {
        const [x, y] = applyH(h, [j / (nc - 1), i / (nr - 1)]);
        const n = i * nc + j;
        expect(v.positions[n * 2]).toBeCloseTo(x, 3);
        expect(v.positions[n * 2 + 1]).toBeCloseTo(y, 3);
      }
    }
  });

  it("uvw divided through returns (u, v)", () => {
    const v = buildMeshVertices({ quad, mesh: identityMesh(2, 2) }, 4);
    const nc = 9;
    const n = 3 * nc + 5;
    expect(v.uvw[n * 3]! / v.uvw[n * 3 + 2]!).toBeCloseTo(5 / 8, 5);
    expect(v.uvw[n * 3 + 1]! / v.uvw[n * 3 + 2]!).toBeCloseTo(3 / 8, 5);
  });

  it("indices cover every fine cell with two triangles", () => {
    const v = buildMeshVertices({ quad, mesh: identityMesh(2, 3) }, 4);
    expect(v.indices.length).toBe(8 * 12 * 6);
    expect(Math.max(...v.indices)).toBe(9 * 13 - 1);
  });

  it("an offset control point moves its vertex by exactly the offset", () => {
    const base = identityMesh(2, 2);
    const mesh = setControlPoint(base, quad, 1, 1, [0, 0]);
    const v0 = buildMeshVertices({ quad, mesh: base }, 4);
    const v1 = buildMeshVertices({ quad, mesh }, 4);
    const n = 4 * 9 + 4; // centre vertex
    const off = mesh.offsets[4]!;
    expect(v1.positions[n * 2]! - v0.positions[n * 2]!).toBeCloseTo(off[0], 3);
    expect(v1.positions[n * 2 + 1]! - v0.positions[n * 2 + 1]!).toBeCloseTo(off[1], 3);
  });
});
