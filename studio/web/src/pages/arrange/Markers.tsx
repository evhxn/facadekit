import { useState, type CSSProperties } from "react";
import type { Marker, Project } from "../../api/types";
import { msToX } from "./timelineMath";

interface Props {
  markers: Marker[];
  ppm: number;
  showOnsets: boolean;
  rulerH: number;
  /** Waveform row, when there is one. */
  wave: { top: number; height: number } | null;
  selectedUserMs: number | null;
  onSelectUser: (t_ms: number) => void;
}

const BEAT = "rgba(255,255,255,0.55)";
const USER = "#f0a020";

/** Beat / onset / user marker overlay for the ruler and the waveform row. */
export default function Markers({
  markers,
  ppm,
  showOnsets,
  rulerH,
  wave,
  selectedUserMs,
  onSelectUser,
}: Props) {
  const regions = [{ top: 0, height: rulerH }, ...(wave ? [wave] : [])];
  return (
    <div data-testid="markers" style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {markers.map((m) => {
        if (m.kind === "onset" && !showOnsets) return null;
        const x = msToX(m.t_ms, ppm);
        if (m.kind === "user") {
          const sel = m.t_ms === selectedUserMs;
          return (
            <div key={`u${m.t_ms}`}>
              {wave ? (
                <div
                  style={{
                    position: "absolute",
                    left: x,
                    top: wave.top,
                    height: wave.height,
                    width: 1,
                    background: USER,
                  }}
                />
              ) : null}
              <div
                role="button"
                aria-label={`User marker at ${(m.t_ms / 1000).toFixed(2)} s`}
                aria-pressed={sel}
                data-marker-ms={m.t_ms}
                style={{
                  position: "absolute",
                  left: x,
                  top: 0,
                  height: rulerH,
                  width: 10,
                  borderLeft: `2px solid ${USER}`,
                  background: sel ? "#fc0" : USER,
                  clipPath: "polygon(0 0, 100% 25%, 0 50%)",
                  pointerEvents: "auto",
                  cursor: "pointer",
                }}
                onPointerDown={(e) => {
                  if (e.button !== 0) return;
                  e.stopPropagation();
                  onSelectUser(m.t_ms);
                }}
              />
            </div>
          );
        }
        const onset = m.kind === "onset";
        return regions.map((r, i) => {
          const h = onset ? Math.min(8, r.height / 2) : r.height;
          const style: CSSProperties = {
            position: "absolute",
            left: x,
            top: r.top + (onset ? r.height - h : 0),
            height: h,
            width: 1,
            background: onset ? "rgba(255,255,255,0.25)" : BEAT,
          };
          return <div key={`${m.kind}${m.t_ms}-${i}`} data-kind={m.kind} style={style} />;
        });
      })}
    </div>
  );
}

/** Context menu for a clip: split on beats, then optionally alternate between surfaces. */
export function BeatMenu({
  project,
  x,
  y,
  stage,
  message,
  onSplit,
  onAlternate,
  onClose,
}: {
  project: Project;
  x: number;
  y: number;
  /** null: split choices; otherwise the ids of the pieces to alternate. */
  stage: string[] | null;
  message: string | null;
  onSplit: (everyN: number) => void;
  onAlternate: (surfaceIds: string[]) => void;
  onClose: () => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const box: CSSProperties = {
    position: "fixed",
    left: x,
    top: y,
    zIndex: 20,
    background: "Canvas",
    color: "CanvasText",
    border: "1px solid #8886",
    borderRadius: 6,
    padding: 6,
    display: "flex",
    flexDirection: "column",
    gap: 4,
    fontSize: 13,
    boxShadow: "0 4px 14px #0004",
  };
  return (
    <div role="menu" aria-label="Clip beats" style={box} onPointerDown={(e) => e.stopPropagation()}>
      {stage === null ? (
        <>
          <button type="button" role="menuitem" onClick={() => onSplit(1)}>
            Split on every beat
          </button>
          <button type="button" role="menuitem" onClick={() => onSplit(2)}>
            Split on every 2 beats
          </button>
          <button type="button" role="menuitem" onClick={() => onSplit(4)}>
            Split on every 4 beats
          </button>
        </>
      ) : project.surfaces.length >= 2 ? (
        <>
          <strong>Alternate between surfaces…</strong>
          {project.surfaces.map((s) => (
            <label key={s.id}>
              <input
                type="checkbox"
                checked={picked.includes(s.id)}
                onChange={(e) =>
                  setPicked((cur) =>
                    e.target.checked ? [...cur, s.id] : cur.filter((id) => id !== s.id),
                  )
                }
              />{" "}
              {s.name}
            </label>
          ))}
          <button type="button" disabled={picked.length < 2} onClick={() => onAlternate(picked)}>
            Alternate
          </button>
        </>
      ) : null}
      {message ? <span style={{ opacity: 0.8 }}>{message}</span> : null}
      <button type="button" onClick={onClose}>
        {stage === null ? "Cancel" : "Done"}
      </button>
    </div>
  );
}
