import type { Mesh, Surface } from "../../api/types";
import { MESH_SIZES, identityMesh, resampleMesh } from "./meshMath";

interface Props {
  surface: Surface;
  onChange: (mesh: Mesh | null) => void;
  testPattern: boolean;
  onTestPattern: (on: boolean) => void;
}

/** Mesh size for a grid-size choice (grids are square: n x n cells). */
export function meshForSize(current: Mesh | null, n: number): Mesh | null {
  if (n <= 0) return null;
  if (!current) return identityMesh(n, n);
  if (current.cols === n && current.rows === n) return current;
  return resampleMesh(current, n, n);
}

/** "Bend (mesh)" section of the surface panel. */
export default function MeshEditor({ surface, onChange, testPattern, onTestPattern }: Props) {
  const mesh = surface.mesh;
  const size = mesh ? mesh.cols : 0;
  return (
    <section aria-label="Bend (mesh)">
      <h2>Bend (mesh)</h2>
      <label>
        Grid size{" "}
        <select value={size} onChange={(e) => onChange(meshForSize(mesh, Number(e.target.value)))}>
          <option value={0}>Off</option>
          {MESH_SIZES.map((n) => (
            <option key={n} value={n}>
              {n}×{n}
            </option>
          ))}
        </select>
      </label>
      {mesh && (
        <div>
          <small>
            Drag the grid points on the canvas, or select one and use the arrow keys (Shift for 10
            px).
          </small>
          <div>
            <button type="button" onClick={() => onChange(identityMesh(mesh.cols, mesh.rows))}>
              Reset bend
            </button>
          </div>
          <label>
            <input
              type="checkbox"
              checked={testPattern}
              onChange={(e) => onTestPattern(e.target.checked)}
            />{" "}
            Test pattern
          </label>
          <small>Draws the bent grid on the projector, so you can match it to the house.</small>
        </div>
      )}
    </section>
  );
}
