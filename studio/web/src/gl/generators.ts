import type { Generator } from "../api/types";

/** Built-in effects (T-109). Must match backend render/generators.py. */

export type GeneratorType = Generator["type"];
export type Rgba = { r: number; g: number; b: number; a: number };

export const GENERATOR_TYPES: GeneratorType[] = ["solid", "wash", "outline", "strobe"];

export const GENERATOR_LABELS: Record<GeneratorType, string> = {
  solid: "Solid colour",
  wash: "Colour wash",
  outline: "Outline trace",
  strobe: "Strobe",
};

export interface ParamSpec {
  key: string;
  label: string;
  kind: "color" | "number";
  def: number | string;
  step?: number;
  min?: number;
}

export const GENERATOR_PARAMS: Record<GeneratorType, ParamSpec[]> = {
  solid: [{ key: "color", label: "Colour", kind: "color", def: "#ffffff" }],
  wash: [
    { key: "color_a", label: "Colour A", kind: "color", def: "#ff0000" },
    { key: "color_b", label: "Colour B", kind: "color", def: "#0000ff" },
    { key: "period_s", label: "Period (s)", kind: "number", def: 4, step: 0.5, min: 0.1 },
    { key: "angle_deg", label: "Angle (deg)", kind: "number", def: 0, step: 5 },
  ],
  outline: [
    { key: "color", label: "Colour", kind: "color", def: "#ffffff" },
    { key: "width", label: "Width", kind: "number", def: 0.03, step: 0.01, min: 0 },
    { key: "speed", label: "Speed (laps/s)", kind: "number", def: 0.5, step: 0.1 },
    { key: "tail", label: "Tail", kind: "number", def: 0.35, step: 0.05, min: 0 },
  ],
  strobe: [
    { key: "color", label: "Colour", kind: "color", def: "#ffffff" },
    { key: "rate_hz", label: "Rate (Hz)", kind: "number", def: 8, step: 1, min: 0 },
    { key: "duty", label: "Duty", kind: "number", def: 0.5, step: 0.05, min: 0 },
  ],
};

/** A generator of `type` with default params filled in. */
export function defaultGenerator(type: GeneratorType): Generator {
  const params: Record<string, number | string> = {};
  for (const p of GENERATOR_PARAMS[type]) params[p.key] = p.def;
  return { type, params };
}

/** "#rrggbb" -> [r, g, b] in 0..1; anything else is white. */
export function parseColor(value: unknown): [number, number, number] {
  if (typeof value === "string" && /^#[0-9a-fA-F]{6}$/.test(value)) {
    return [
      parseInt(value.slice(1, 3), 16) / 255,
      parseInt(value.slice(3, 5), 16) / 255,
      parseInt(value.slice(5, 7), 16) / 255,
    ];
  }
  return [1, 1, 1];
}

export function numParam(gen: Generator, key: string, def: number): number {
  const raw = gen.params[key];
  const v = typeof raw === "string" ? parseFloat(raw) : raw;
  return typeof v === "number" && Number.isFinite(v) ? v : def;
}

const fract = (x: number) => x - Math.floor(x);

/** Colour (0..1, non-premultiplied) and alpha at (u, v) in the surface unit square, clip time tc (s). */
export function evalGenerator(gen: Generator, u: number, v: number, tc: number): Rgba {
  switch (gen.type) {
    case "solid": {
      const [r, g, b] = parseColor(gen.params.color ?? "#ffffff");
      return { r, g, b, a: 1 };
    }
    case "wash": {
      const a = (numParam(gen, "angle_deg", 0) * Math.PI) / 180;
      const period = numParam(gen, "period_s", 4) || 4;
      const p = u * Math.cos(a) + v * Math.sin(a);
      const m = 0.5 + 0.5 * Math.sin(2 * Math.PI * (p - tc / period));
      const ca = parseColor(gen.params.color_a ?? "#ff0000");
      const cb = parseColor(gen.params.color_b ?? "#0000ff");
      return {
        r: ca[0] + (cb[0] - ca[0]) * m,
        g: ca[1] + (cb[1] - ca[1]) * m,
        b: ca[2] + (cb[2] - ca[2]) * m,
        a: 1,
      };
    }
    case "outline": {
      const width = numParam(gen, "width", 0.03);
      const speed = numParam(gen, "speed", 0.5);
      const tail = numParam(gen, "tail", 0.35);
      const [r, g, b] = parseColor(gen.params.color ?? "#ffffff");
      const dists = [v, 1 - u, 1 - v, u]; // top, right, bottom, left
      let edge = 0;
      for (let i = 1; i < 4; i++) if (dists[i]! < dists[edge]!) edge = i;
      if (!(dists[edge]! < width)) return { r, g, b, a: 0 };
      const s = [0.25 * u, 0.25 + 0.25 * v, 0.5 + 0.25 * (1 - u), 0.75 + 0.25 * (1 - v)][edge]!;
      const behind = fract(fract(tc * speed) - s);
      return { r, g, b, a: tail > 0 && behind < tail ? 1 - behind / tail : 0 };
    }
    default: {
      const [r, g, b] = parseColor(gen.params.color ?? "#ffffff");
      const on = fract(tc * numParam(gen, "rate_hz", 8)) < numParam(gen, "duty", 0.5);
      return { r, g, b, a: on ? 1 : 0 };
    }
  }
}

export const GEN_TYPE_ID: Record<GeneratorType, number> = {
  solid: 1,
  wash: 2,
  outline: 3,
  strobe: 4,
};

export interface GeneratorUniforms {
  type: number;
  colA: [number, number, number];
  colB: [number, number, number];
  /** wash: (period_s, angle_rad); outline: (width, speed, tail); strobe: (rate_hz, duty). */
  p: [number, number, number, number];
  tc: number;
}

export function generatorUniforms(gen: Generator, tc: number): GeneratorUniforms {
  const type = GEN_TYPE_ID[gen.type];
  let colA: [number, number, number];
  let colB: [number, number, number] = [0, 0, 0];
  let p: [number, number, number, number] = [0, 0, 0, 0];
  if (gen.type === "wash") {
    colA = parseColor(gen.params.color_a ?? "#ff0000");
    colB = parseColor(gen.params.color_b ?? "#0000ff");
    p = [numParam(gen, "period_s", 4) || 4, (numParam(gen, "angle_deg", 0) * Math.PI) / 180, 0, 0];
  } else {
    colA = parseColor(gen.params.color ?? "#ffffff");
    if (gen.type === "outline") {
      p = [
        numParam(gen, "width", 0.03),
        numParam(gen, "speed", 0.5),
        numParam(gen, "tail", 0.35),
        0,
      ];
    } else if (gen.type === "strobe") {
      p = [numParam(gen, "rate_hz", 8), numParam(gen, "duty", 0.5), 0, 0];
    }
  }
  return { type, colA, colB, p, tc };
}

/** GLSL: sets `vec4 c` (non-premultiplied) from `uv` in the surface unit square. */
export const GEN_GLSL = `
uniform int u_genType;    // 0 = texture, 1 solid, 2 wash, 3 outline, 4 strobe
uniform vec3 u_genColA;
uniform vec3 u_genColB;
uniform vec4 u_genP;
uniform float u_genTc;    // clip-local time in seconds
vec4 genColor(vec2 uv) {
  float u = uv.x;
  float v = uv.y;
  if (u_genType == 1) return vec4(u_genColA, 1.0);
  if (u_genType == 2) {
    float p = u * cos(u_genP.y) + v * sin(u_genP.y);
    float m = 0.5 + 0.5 * sin(6.283185307179586 * (p - u_genTc / u_genP.x));
    return vec4(mix(u_genColA, u_genColB, m), 1.0);
  }
  if (u_genType == 3) {
    float dt = v;
    float dr = 1.0 - u;
    float db = 1.0 - v;
    float dl = u;
    float d = dt;
    float s = 0.25 * u;
    if (dr < d) { d = dr; s = 0.25 + 0.25 * v; }
    if (db < d) { d = db; s = 0.5 + 0.25 * (1.0 - u); }
    if (dl < d) { d = dl; s = 0.75 + 0.25 * (1.0 - v); }
    float a = 0.0;
    if (d < u_genP.x && u_genP.z > 0.0) {
      float head = fract(u_genTc * u_genP.y);
      float behind = fract(head - s);
      a = behind < u_genP.z ? 1.0 - behind / u_genP.z : 0.0;
    }
    return vec4(u_genColA, a);
  }
  float on = fract(u_genTc * u_genP.x) < u_genP.y ? 1.0 : 0.0;
  return vec4(u_genColA, on);
}
`;
