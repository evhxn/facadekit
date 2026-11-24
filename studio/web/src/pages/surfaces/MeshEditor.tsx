import type { Mesh, Surface } from "../../api/types";
import { MESH_SIZES, identityMesh, resampleMesh } from "./meshMath";
import InfoTip from "../../components/InfoTip";

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
      <h2>
        Bend to fit
        <InfoTip term="Bend to fit">
          For a surface that is not flat, such as a curved wall or a column. Pick a grid size, then
          drag the grid points on the picture until the grid follows the surface. Most doors and
          windows do not need this.
        </InfoTip>
      </h2>
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
            Show the grid on the projector
          </label>
          <small>Match the projected grid to the house while you bend.</small>
        </div>
      )}
    </section>
  );
}
