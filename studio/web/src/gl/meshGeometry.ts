import type { Surface } from "../api/types";
import { applyH, quadHomography, type Mat3 } from "./homography";
import { interpolateOffset, identityMesh } from "../pages/surfaces/meshMath";

export interface MeshVertices {
  /** Final projector px, 2 floats per vertex. */
  positions: Float32Array;
  /** Perspective-correct surface coordinates (u/w, v/w, 1/w), 3 floats per vertex. */
  uvw: Float32Array;
  indices: Uint16Array;
}

/**
 * Tessellated grid for a meshed surface: each mesh cell is split subdiv x subdiv, vertex
 * position = homography(u, v) + bilinear offset. The shader divides uvw.xy by uvw.z.
 *
 * The screen-space-affine quantity under a homography is (u, v, 1) / w, where w is the third
 * component of H * (u, v, 1) (not (u*w, v*w, w), which is only affine when w == 1).
 * Triangles use the backend's diagonal (p00, p01, p10) + (p01, p11, p10).
 */
export function buildMeshVertices(
  surface: Pick<Surface, "quad" | "mesh">,
  subdiv = 4,
): MeshVertices {
  const mesh = surface.mesh ?? identityMesh(1, 1);
  const nc = mesh.cols * subdiv + 1;
  const nr = mesh.rows * subdiv + 1;
  const h: Mat3 = quadHomography(1, 1, surface.quad);
  const positions = new Float32Array(nc * nr * 2);
  const uvw = new Float32Array(nc * nr * 3);
  for (let i = 0; i < nr; i++) {
    for (let j = 0; j < nc; j++) {
      const u = j / (nc - 1);
      const v = i / (nr - 1);
      const base = applyH(h, [u, v]);
      const off = interpolateOffset(mesh, u, v);
      const n = i * nc + j;
      positions[n * 2] = base[0] + off[0];
      positions[n * 2 + 1] = base[1] + off[1];
      const q = 1 / (h[6] * u + h[7] * v + h[8]);
      uvw[n * 3] = u * q;
      uvw[n * 3 + 1] = v * q;
      uvw[n * 3 + 2] = q;
    }
  }
  const indices = new Uint16Array((nc - 1) * (nr - 1) * 6);
  let k = 0;
  for (let i = 0; i < nr - 1; i++) {
    for (let j = 0; j < nc - 1; j++) {
      const p00 = i * nc + j;
      const p01 = p00 + 1;
      const p10 = p00 + nc;
      const p11 = p10 + 1;
      indices.set([p00, p01, p10, p01, p11, p10], k);
      k += 6;
    }
  }
  return { positions, uvw, indices };
}
