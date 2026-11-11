import type { Mask, Pt, Surface } from "../../api/types";
import { MIN_VERTICES, isSimple, removeVertex, translate } from "./polygon";

/** Custom outline initialised from the quad corners. */
export function maskFromQuad(quad: Surface["quad"]): Mask {
  return { outer: quad.map((p) => [p[0], p[1]] as Pt), holes: [] };
}

/** `ring` -1 is the outer outline, k >= 0 is hole k. Never goes below 3 points. */
export function removeMaskVertex(mask: Mask, ring: number, i: number): Mask {
  if (ring < 0) return { ...mask, outer: removeVertex(mask.outer, i) };
  return { ...mask, holes: mask.holes.map((h, k) => (k === ring ? removeVertex(h, i) : h)) };
}

export function addHole(mask: Mask, hole: Pt[]): Mask {
  if (hole.length < MIN_VERTICES) return mask;
  return { ...mask, holes: [...mask.holes, hole.map((p) => [p[0], p[1]] as Pt)] };
}

export function removeHole(mask: Mask, k: number): Mask {
  return { ...mask, holes: mask.holes.filter((_, i) => i !== k) };
}

export function translateMask(mask: Mask, dx: number, dy: number): Mask {
  return {
    outer: translate(mask.outer, dx, dy),
    holes: mask.holes.map((h) => translate(h, dx, dy)),
  };
}

export function maskIsSimple(mask: Mask): boolean {
  return isSimple(mask.outer) && mask.holes.every(isSimple);
}

interface Props {
  surface: Surface;
  onChange: (mask: Mask | null) => void;
}

/** "Shape" section of the surface panel. */
export default function MaskEditor({ surface, onChange }: Props) {
  const mask = surface.mask;
  return (
    <section aria-label="Shape">
      <h2>Shape</h2>
      <label>
        <input
          type="radio"
          name={`shape-${surface.id}`}
          checked={mask === null}
          onChange={() => onChange(null)}
        />{" "}
        Use corners (default)
      </label>
      <br />
      <label>
        <input
          type="radio"
          name={`shape-${surface.id}`}
          checked={mask !== null}
          onChange={() => {
            if (mask === null) onChange(maskFromQuad(surface.quad));
          }}
        />{" "}
        Custom outline
      </label>
      {mask && (
        <div>
          <small>
            Drag points, click an edge to add one, Delete removes the selected point (at least 3
            stay).
          </small>
          {!maskIsSimple(mask) && <p role="alert">The outline crosses itself. Drag its points apart until the lines no longer cross.</p>}
          <ul>
            {mask.holes.map((_, k) => (
              <li key={k}>
                Hole {k + 1}{" "}
                <button type="button" onClick={() => onChange(removeHole(mask, k))}>
                  Remove hole {k + 1}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
