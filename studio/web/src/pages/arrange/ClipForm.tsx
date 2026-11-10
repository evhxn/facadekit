import { useEffect, useState } from "react";
import type { Asset, Clip, Generator, Surface, Track } from "../../api/types";
import {
  GENERATOR_LABELS,
  GENERATOR_PARAMS,
  GENERATOR_TYPES,
  defaultGenerator,
  type GeneratorType,
  type ParamSpec,
} from "../../gl/generators";
import styles from "./ClipForm.module.css";

const FIT_HELP: Record<Clip["fit"], string> = {
  fill: "Fill: cover the whole surface and crop the overflow.",
  fit: "Fit: show the whole picture with empty bars if the shape differs.",
  stretch: "Stretch: squeeze the picture to match the surface shape.",
};

export const CANVAS_ID = "canvas";

/** Seconds field with one decimal; stores integer milliseconds via onCommit. */
export function SecondsInput({
  label,
  valueMs,
  onCommit,
  min = 0,
  minExclusive = false,
}: {
  label: string;
  valueMs: number;
  onCommit: (ms: number) => void;
  min?: number;
  minExclusive?: boolean;
}) {
  const [text, setText] = useState((valueMs / 1000).toFixed(1));
  useEffect(() => {
    setText((prev) =>
      Math.round(parseFloat(prev) * 1000) === valueMs ? prev : (valueMs / 1000).toFixed(1),
    );
  }, [valueMs]);
  return (
    <label>
      {label}
      <input
        type="number"
        step={0.1}
        min={min}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          const s = parseFloat(e.target.value);
          if (!Number.isFinite(s)) return;
          const ms = Math.round(s * 1000);
          if (ms < min * 1000 || (minExclusive && ms <= min * 1000)) return;
          onCommit(ms);
        }}
      />
    </label>
  );
}

function NumberParam({
  spec,
  value,
  onCommit,
}: {
  spec: ParamSpec;
  value: number;
  onCommit: (v: number) => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => {
    setText((prev) => (parseFloat(prev) === value ? prev : String(value)));
  }, [value]);
  return (
    <label>
      {spec.label}
      <input
        type="number"
        step={spec.step}
        min={spec.min}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          const v = parseFloat(e.target.value);
          if (Number.isFinite(v) && (spec.min === undefined || v >= spec.min)) onCommit(v);
        }}
      />
    </label>
  );
}

function EffectFields({
  generator,
  onChange,
}: {
  generator: Generator;
  onChange: (g: Generator) => void;
}) {
  const setParam = (key: string, v: number | string) =>
    onChange({ ...generator, params: { ...generator.params, [key]: v } });
  return (
    <>
      <label>
        Effect
        <select
          value={generator.type}
          onChange={(e) => onChange(defaultGenerator(e.target.value as GeneratorType))}
        >
          {GENERATOR_TYPES.map((t) => (
            <option key={t} value={t}>
              {GENERATOR_LABELS[t]}
            </option>
          ))}
        </select>
      </label>
      <div className={styles.row}>
        {GENERATOR_PARAMS[generator.type].map((spec) => {
          const raw = generator.params[spec.key] ?? spec.def;
          return spec.kind === "color" ? (
            <label key={spec.key}>
              {spec.label}
              <input
                type="color"
                value={typeof raw === "string" && /^#[0-9a-fA-F]{6}$/.test(raw) ? raw : "#ffffff"}
                onChange={(e) => setParam(spec.key, e.target.value)}
              />
            </label>
          ) : (
            <NumberParam
              key={spec.key}
              spec={spec}
              value={typeof raw === "number" ? raw : Number(raw) || 0}
              onCommit={(v) => setParam(spec.key, v)}
            />
          );
        })}
      </div>
    </>
  );
}

export default function ClipForm({
  clip,
  assets,
  surfaces,
  tracks,
  layer,
  isNew,
  message,
  onPatch,
  onLayer,
  onSubmit,
  onCancel,
  onRemove,
}: {
  clip: Clip;
  assets: Asset[];
  surfaces: Surface[];
  tracks: Track[];
  /** Index of the layer (track) the clip is on, or "new". */
  layer: number | "new";
  isNew: boolean;
  message: string | null;
  onPatch: (patch: Partial<Clip>) => void;
  onLayer: (layer: number | "new") => void;
  onSubmit?: () => void;
  onCancel?: () => void;
  onRemove?: () => void;
}) {
  const isEffect = clip.generator !== null;
  const visual = assets.filter((a) => a.kind !== "audio");
  const surfaceKnown =
    clip.surface_id === CANVAS_ID || surfaces.some((s) => s.id === clip.surface_id);
  return (
    <form
      className={styles.form}
      aria-label="Clip"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit?.();
      }}
    >
      <fieldset className={styles.source}>
        <legend>Source</legend>
        <label className={styles.check}>
          <input
            type="radio"
            name="clip-source"
            checked={!isEffect}
            onChange={() => onPatch({ asset_id: visual[0]?.id ?? null, generator: null })}
          />
          Media
        </label>
        <label className={styles.check}>
          <input
            type="radio"
            name="clip-source"
            checked={isEffect}
            onChange={() => onPatch({ asset_id: null, generator: defaultGenerator("solid") })}
          />
          Effect
        </label>
      </fieldset>
      {clip.generator ? (
        <EffectFields generator={clip.generator} onChange={(generator) => onPatch({ generator })} />
      ) : (
        <label>
          Media
          <select
            value={clip.asset_id ?? ""}
            onChange={(e) => onPatch({ asset_id: e.target.value })}
          >
            {visual.map((a) => (
              <option key={a.id} value={a.id}>
                {a.original_name}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Surface
        <select value={clip.surface_id} onChange={(e) => onPatch({ surface_id: e.target.value })}>
          {surfaces.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
          <option value={CANVAS_ID}>Whole frame (canvas)</option>
          {!surfaceKnown ? <option value={clip.surface_id}>Missing surface</option> : null}
        </select>
      </label>
      <label>
        Layer
        <select
          value={String(layer)}
          onChange={(e) => onLayer(e.target.value === "new" ? "new" : Number(e.target.value))}
        >
          {tracks.map((t, i) => (
            <option key={t.id} value={i}>
              {`Track ${i + 1}`}
            </option>
          ))}
          <option value="new">New layer on top</option>
        </select>
      </label>
      <div className={styles.row}>
        <SecondsInput
          label="Start (s)"
          valueMs={clip.start_ms}
          onCommit={(ms) => onPatch({ start_ms: ms })}
        />
        <SecondsInput
          label="Duration (s)"
          valueMs={clip.duration_ms}
          minExclusive
          onCommit={(ms) => onPatch({ duration_ms: ms })}
        />
        {isEffect ? null : (
          <SecondsInput
            label="In offset (s)"
            valueMs={clip.in_offset_ms}
            onCommit={(ms) => onPatch({ in_offset_ms: ms })}
          />
        )}
      </div>
      {isEffect ? null : (
        <>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={clip.loop}
              onChange={(e) => onPatch({ loop: e.target.checked })}
            />
            Loop
          </label>
          <label>
            Fit
            <select
              value={clip.fit}
              onChange={(e) => onPatch({ fit: e.target.value as Clip["fit"] })}
            >
              <option value="fill">Fill</option>
              <option value="fit">Fit</option>
              <option value="stretch">Stretch</option>
            </select>
            <small>{FIT_HELP[clip.fit]}</small>
          </label>
        </>
      )}
      <label>
        Opacity ({Math.round(clip.opacity * 100)} %)
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={clip.opacity}
          onChange={(e) => onPatch({ opacity: Math.min(1, Math.max(0, Number(e.target.value))) })}
        />
      </label>
      <div className={styles.row}>
        <SecondsInput
          label="Fade in (s)"
          valueMs={clip.fade_in_ms}
          onCommit={(ms) => onPatch({ fade_in_ms: ms })}
        />
        <SecondsInput
          label="Fade out (s)"
          valueMs={clip.fade_out_ms}
          onCommit={(ms) => onPatch({ fade_out_ms: ms })}
        />
      </div>
      <label>
        Blend
        <select
          value={clip.blend}
          onChange={(e) => onPatch({ blend: e.target.value as Clip["blend"] })}
        >
          <option value="normal">Normal</option>
          <option value="add">Add</option>
          <option value="screen">Screen</option>
        </select>
        <small>Use Add or Screen for videos with a black background</small>
      </label>
      {message ? (
        <p role="alert" className={styles.message}>
          {message}
        </p>
      ) : null}
      <div className={styles.row}>
        {isNew ? (
          <>
            <button type="submit">Add to timeline</button>
            <button type="button" onClick={onCancel}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button type="button" onClick={onCancel}>
              Close
            </button>
            <button type="button" onClick={onRemove}>
              Remove clip
            </button>
          </>
        )}
      </div>
    </form>
  );
}
