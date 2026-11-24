import type { Generator } from "../api/types";
import specJson from "./generatorParams.json";

/**
 * Built-in effects (T-109 and later). Must match backend render/generators.py: the parity
 * test compares the two. Random parts use the same 32-bit integer hash in numpy, here and in
 * GLSL, so a seeded starfield looks the same in the preview and in the render.
 */

export type GeneratorType = Generator["type"];
export type Rgba = { r: number; g: number; b: number; a: number };

export interface ParamSpec {
  key: string;
  label: string;
  kind: "color" | "number";
  def: number | string;
  step?: number;
  min?: number;
  max?: number;
}

interface TypeSpec {
  label: string;
  /** Renderers clamp numbers to [min, max] (the effects added after T-109). */
  clamp: boolean;
  params: ParamSpec[];
}

/** One table for the UI, the preview and (by a backend test) the Python renderer. */
const SPECS = specJson as Record<GeneratorType, TypeSpec>;

export const GENERATOR_TYPES: GeneratorType[] = [
  "solid",
  "wash",
  "outline",
  "chase",
  "rings",
  "sweep",
  "cycle",
  "plasma",
  "stars",
  "snow",
  "flicker",
  "strobe",
];

export const GENERATOR_LABELS = Object.fromEntries(
  GENERATOR_TYPES.map((t) => [t, SPECS[t].label]),
) as Record<GeneratorType, string>;

export const GENERATOR_PARAMS = Object.fromEntries(
  GENERATOR_TYPES.map((t) => [t, SPECS[t].params]),
) as Record<GeneratorType, ParamSpec[]>;

/** Flashing faster than this may trigger seizures in people with photosensitive epilepsy. */
export const SAFE_FLASH_HZ = 3;

/** A warning to show for these params, or null (strobe above SAFE_FLASH_HZ). */
export function flashWarning(gen: Generator): string | null {
  if (gen.type !== "strobe") return null;
  const rate = numParam(gen, "rate_hz", 8);
  if (!(rate > SAFE_FLASH_HZ)) return null;
  return (
    `Flashes ${rate} times a second. Flashing faster than ${SAFE_FLASH_HZ} times a second ` +
    "can trigger seizures in people with photosensitive epilepsy, and passers-by cannot " +
    "look away from a house. Use Gentle flicker, or 3 Hz or less."
  );
}

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

/** A number of a clamped type: the spec default if missing or bad, clamped to its range. */
export function param(gen: Generator, key: string): number {
  const spec = SPECS[gen.type].params.find((p) => p.key === key);
  if (!spec || typeof spec.def !== "number") throw new Error(`no number param ${key}`);
  let v = numParam(gen, key, spec.def);
  if (spec.min !== undefined) v = Math.max(spec.min, v);
  if (spec.max !== undefined) v = Math.min(spec.max, v);
  return v;
}

const intParam = (gen: Generator, key: string) => Math.floor(param(gen, key) + 0.5);

function colorParam(gen: Generator, key: string): [number, number, number] {
  const spec = SPECS[gen.type].params.find((p) => p.key === key);
  return parseColor(gen.params[key] ?? spec?.def);
}

/** 32-bit integer finaliser (numpy `_mix`, GLSL `fkMix`). */
export function mix32(x: number): number {
  let h = x >>> 0;
  h = (h ^ (h >>> 16)) >>> 0;
  h = Math.imul(h, 0x7feb352d) >>> 0;
  h = (h ^ (h >>> 15)) >>> 0;
  h = Math.imul(h, 0x846ca68b) >>> 0;
  return (h ^ (h >>> 16)) >>> 0;
}

/** Hash of integer cell (a, b) and a seed (numpy `cell_hash`, GLSL `fkHash`). */
export function cellHash(a: number, b: number, seed: number): number {
  return mix32(
    (Math.imul(a, 0x8da6b343) ^ Math.imul(b, 0xd8163841) ^ Math.imul(seed, 0xcb1ab31f)) >>> 0,
  );
}

/** k-th number in [0, 1) from hash h, 24 bits (numpy `rnd`, GLSL `fkRnd`). */
export function rnd(h: number, k: number): number {
  return (mix32((h + Math.imul(k, 0x9e3779b9)) >>> 0) >>> 8) / 16777216;
}

const smoothstep = (e0: number, e1: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
};

/** Alpha of `flicker` at clip time tc: smooth steps between seeded random levels. */
export function flickerLevel(gen: Generator, tc: number): number {
  const n = Math.max(tc, 0) * param(gen, "rate_hz");
  const seed = intParam(gen, "seed");
  const i = Math.floor(n);
  const f = n - i;
  const a0 = rnd(cellHash(i, 0, seed), 0);
  const a1 = rnd(cellHash(i + 1, 0, seed), 0);
  return 1 - param(gen, "depth") * (a0 + (a1 - a0) * f * f * (3 - 2 * f));
}

/** Centre of the sweep bar along its direction at clip time tc. */
function sweepCentre(gen: Generator, tc: number): [number, number, number] {
  const width = param(gen, "width");
  const ang = (param(gen, "angle_deg") * Math.PI) / 180;
  const c = Math.cos(ang);
  const s = Math.sin(ang);
  const lo = Math.min(0, c) + Math.min(0, s);
  const hi = Math.max(0, c) + Math.max(0, s);
  return [lo - width + (hi - lo + 2 * width) * fract(tc / param(gen, "period_s")), c, s];
}

function dots(
  x: number,
  y: number,
  size: number,
  seed: number,
  present: number,
  tc: number,
  twinkle: number,
  sway: number,
): number {
  const ci = Math.floor(x);
  const cj = Math.floor(y);
  const h = cellHash(ci + 1048576, cj + 1048576, seed);
  if (!(rnd(h, 0) < present)) return 0;
  let jx = 2 * rnd(h, 1) - 1;
  if (sway > 0) jx = jx * (1 - sway) + sway * Math.sin(2 * Math.PI * (tc * 0.25 + rnd(h, 3)));
  const cx = 0.5 + (0.5 - size) * jx;
  const cy = size + (1 - 2 * size) * rnd(h, 2);
  const d = Math.hypot(x - ci - cx, y - cj - cy) / size;
  let a = 1 - smoothstep(0, 1, d);
  if (twinkle > 0) {
    const rate = twinkle * (0.5 + rnd(h, 3));
    a *= 0.55 + 0.45 * Math.sin(2 * Math.PI * (tc * rate + rnd(h, 4)));
  }
  return a;
}

const mixRgb = (
  ca: [number, number, number],
  cb: [number, number, number],
  m: number,
  a = 1,
): Rgba => ({
  r: ca[0] + (cb[0] - ca[0]) * m,
  g: ca[1] + (cb[1] - ca[1]) * m,
  b: ca[2] + (cb[2] - ca[2]) * m,
  a,
});

function evalNew(gen: Generator, u: number, v: number, tc: number, aspect: number): Rgba {
  const A = aspect;
  switch (gen.type) {
    case "chase": {
      const [r, g, b] = colorParam(gen, "color");
      const width = param(gen, "width");
      const tot = 2 * A + 2;
      const cand: [number, number][] = [
        [v, (u * A) / tot],
        [(1 - u) * A, (A + v) / tot],
        [1 - v, (A + 1 + (1 - u) * A) / tot],
        [u * A, (2 * A + 1 + (1 - v)) / tot],
      ];
      let best = cand[0]!;
      for (const c of cand.slice(1)) if (c[0] < best[0]) best = c;
      const [d, s] = best;
      const m =
        0.5 +
        0.5 * Math.cos(2 * Math.PI * (s * intParam(gen, "dashes") - tc * param(gen, "speed")));
      return { r, g, b, a: smoothstep(0.35, 0.65, m) * (1 - smoothstep(width - 0.01, width, d)) };
    }
    case "rings": {
      const rr = Math.hypot((u - 0.5) * A, v - 0.5);
      const m =
        0.5 + 0.5 * Math.cos(2 * Math.PI * (rr * param(gen, "count") - tc * param(gen, "speed")));
      return mixRgb(colorParam(gen, "color_a"), colorParam(gen, "color_b"), m);
    }
    case "stars": {
      const [r, g, b] = colorParam(gen, "color");
      const dens = param(gen, "density");
      const a = dots(
        u * A * dens,
        v * dens,
        param(gen, "size"),
        intParam(gen, "seed"),
        0.55,
        tc,
        param(gen, "twinkle_hz"),
        0,
      );
      return { r, g, b, a };
    }
    case "snow": {
      const [r, g, b] = colorParam(gen, "color");
      const dens = param(gen, "density");
      const seed = intParam(gen, "seed");
      const x = u * A * dens;
      const col = rnd(cellHash(Math.floor(x) + 1048576, 0, seed ^ 0x5a5a), 0);
      const y = v * dens - tc * param(gen, "speed") * dens * (0.7 + 0.6 * col) + 7 * col;
      const a = dots(x, y, param(gen, "size"), seed, 0.5, tc, 0, param(gen, "drift"));
      return { r, g, b, a };
    }
    case "sweep": {
      const [r, g, b] = colorParam(gen, "color");
      const [c, cs, sn] = sweepCentre(gen, tc);
      return { r, g, b, a: 1 - smoothstep(0, param(gen, "width"), Math.abs(u * cs + v * sn - c)) };
    }
    case "cycle": {
      const ang = (param(gen, "angle_deg") * Math.PI) / 180;
      const hue =
        (u * Math.cos(ang) + v * Math.sin(ang)) * param(gen, "spread") +
        tc / param(gen, "period_s");
      const sat = param(gen, "saturation");
      const val = param(gen, "brightness");
      const ch = (k: number) => {
        const pure = Math.min(1, Math.max(0, Math.abs(fract(hue + k) * 6 - 3) - 1));
        return val * (1 - sat + sat * pure);
      };
      return { r: ch(0), g: ch(2 / 3), b: ch(1 / 3), a: 1 };
    }
    case "plasma": {
      const sc = param(gen, "scale");
      const t = tc * param(gen, "speed");
      const x = (u - 0.5) * A * sc;
      const y = (v - 0.5) * sc;
      const val =
        Math.sin(x * 2 + t * 1.7) +
        Math.sin(y * 2.3 - t * 1.3) +
        Math.sin((x + y) * 1.4 + t) +
        Math.sin(Math.hypot(x, y) * 2.6 - t * 2.1);
      return mixRgb(
        colorParam(gen, "color_a"),
        colorParam(gen, "color_b"),
        0.5 + 0.5 * Math.sin(val * 1.2),
      );
    }
    default: {
      const [r, g, b] = colorParam(gen, "color");
      return { r, g, b, a: flickerLevel(gen, tc) };
    }
  }
}

/**
 * Colour (0..1, non-premultiplied) and alpha at (u, v) in the surface unit square, clip time
 * tc (s). `aspect` is the surface width / height (the newer effects keep circles round).
 */
export function evalGenerator(gen: Generator, u: number, v: number, tc: number, aspect = 1): Rgba {
  if (SPECS[gen.type].clamp) return evalNew(gen, u, v, tc, aspect > 0 ? aspect : 1);
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
      return mixRgb(
        parseColor(gen.params.color_a ?? "#ff0000"),
        parseColor(gen.params.color_b ?? "#0000ff"),
        m,
      );
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
  chase: 5,
  rings: 6,
  stars: 7,
  snow: 8,
  sweep: 9,
  cycle: 10,
  plasma: 11,
  flicker: 12,
};

type Vec4 = [number, number, number, number];

export interface GeneratorUniforms {
  type: number;
  colA: [number, number, number];
  colB: [number, number, number];
  /** Per type, see GEN_GLSL. */
  p: Vec4;
  q: Vec4;
  aspect: number;
  tc: number;
}

export function generatorUniforms(gen: Generator, tc: number, aspect = 1): GeneratorUniforms {
  const type = GEN_TYPE_ID[gen.type];
  let colA: [number, number, number] = [1, 1, 1];
  let colB: [number, number, number] = [0, 0, 0];
  let p: Vec4 = [0, 0, 0, 0];
  const q: Vec4 = [0, 0, 0, 0];
  switch (gen.type) {
    case "wash":
      colA = parseColor(gen.params.color_a ?? "#ff0000");
      colB = parseColor(gen.params.color_b ?? "#0000ff");
      p = [
        numParam(gen, "period_s", 4) || 4,
        (numParam(gen, "angle_deg", 0) * Math.PI) / 180,
        0,
        0,
      ];
      break;
    case "solid":
    case "outline":
    case "strobe":
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
      break;
    case "chase":
      colA = colorParam(gen, "color");
      p = [param(gen, "width"), intParam(gen, "dashes"), param(gen, "speed"), 0];
      break;
    case "rings":
    case "plasma":
      colA = colorParam(gen, "color_a");
      colB = colorParam(gen, "color_b");
      p =
        gen.type === "rings"
          ? [param(gen, "count"), param(gen, "speed"), 0, 0]
          : [param(gen, "scale"), param(gen, "speed"), 0, 0];
      break;
    case "stars":
      colA = colorParam(gen, "color");
      p = [
        param(gen, "density"),
        param(gen, "size"),
        param(gen, "twinkle_hz"),
        intParam(gen, "seed"),
      ];
      break;
    case "snow":
      colA = colorParam(gen, "color");
      p = [param(gen, "density"), param(gen, "size"), param(gen, "speed"), param(gen, "drift")];
      q[0] = intParam(gen, "seed");
      break;
    case "sweep": {
      colA = colorParam(gen, "color");
      const [c, cs, sn] = sweepCentre(gen, tc);
      p = [param(gen, "width"), c, cs, sn];
      break;
    }
    case "cycle": {
      const ang = (param(gen, "angle_deg") * Math.PI) / 180;
      p = [Math.cos(ang), Math.sin(ang), param(gen, "spread"), tc / param(gen, "period_s")];
      q[0] = param(gen, "saturation");
      q[1] = param(gen, "brightness");
      break;
    }
    case "flicker":
      colA = colorParam(gen, "color");
      p = [flickerLevel(gen, tc), 0, 0, 0];
      break;
  }
  return { type, colA, colB, p, q, aspect: aspect > 0 ? aspect : 1, tc };
}

/** GLSL: sets `vec4 c` (non-premultiplied) from `uv` in the surface unit square. */
export const GEN_GLSL = `
uniform int u_genType;    // 0 = texture, 1 solid, 2 wash, 3 outline, 4 strobe, 5.. see GEN_TYPE_ID
uniform vec3 u_genColA;
uniform vec3 u_genColB;
uniform vec4 u_genP;
uniform vec4 u_genQ;
uniform float u_genAspect; // surface width / height
uniform float u_genTc;    // clip-local time in seconds
const float FK_TAU = 6.283185307179586;
uint fkMix(uint h) {
  h ^= h >> 16u; h *= 0x7feb352du; h ^= h >> 15u; h *= 0x846ca68bu; h ^= h >> 16u;
  return h;
}
uint fkHash(int a, int b, uint seed) {
  return fkMix((uint(a) * 0x8da6b343u) ^ (uint(b) * 0xd8163841u) ^ (seed * 0xcb1ab31fu));
}
float fkRnd(uint h, uint k) {
  return float(fkMix(h + k * 0x9e3779b9u) >> 8u) * (1.0 / 16777216.0);
}
float fkDots(vec2 xy, float size, uint seed, float present, float twinkle, float sway) {
  vec2 cell = floor(xy);
  vec2 f = xy - cell;
  uint h = fkHash(int(cell.x) + 1048576, int(cell.y) + 1048576, seed);
  if (!(fkRnd(h, 0u) < present)) return 0.0;
  float jx = 2.0 * fkRnd(h, 1u) - 1.0;
  if (sway > 0.0) jx = jx * (1.0 - sway) + sway * sin(FK_TAU * (u_genTc * 0.25 + fkRnd(h, 3u)));
  float cx = 0.5 + (0.5 - size) * jx;
  float cy = size + (1.0 - 2.0 * size) * fkRnd(h, 2u);
  float a = 1.0 - smoothstep(0.0, 1.0, length(f - vec2(cx, cy)) / size);
  if (twinkle > 0.0) {
    float rate = twinkle * (0.5 + fkRnd(h, 3u));
    a *= 0.55 + 0.45 * sin(FK_TAU * (u_genTc * rate + fkRnd(h, 4u)));
  }
  return a;
}
vec4 genColor(vec2 uv) {
  float u = uv.x;
  float v = uv.y;
  float A = u_genAspect;
  if (u_genType == 1) return vec4(u_genColA, 1.0);
  if (u_genType == 2) {
    float p = u * cos(u_genP.y) + v * sin(u_genP.y);
    float m = 0.5 + 0.5 * sin(FK_TAU * (p - u_genTc / u_genP.x));
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
  if (u_genType == 4) {
    float on = fract(u_genTc * u_genP.x) < u_genP.y ? 1.0 : 0.0;
    return vec4(u_genColA, on);
  }
  if (u_genType == 5) {  // chase: p = (width, dashes, speed)
    float tot = 2.0 * A + 2.0;
    float d = v;
    float s = u * A / tot;
    float dr = (1.0 - u) * A;
    if (dr < d) { d = dr; s = (A + v) / tot; }
    float db = 1.0 - v;
    if (db < d) { d = db; s = (A + 1.0 + (1.0 - u) * A) / tot; }
    float dl = u * A;
    if (dl < d) { d = dl; s = (2.0 * A + 1.0 + (1.0 - v)) / tot; }
    float m = 0.5 + 0.5 * cos(FK_TAU * (s * u_genP.y - u_genTc * u_genP.z));
    float a = smoothstep(0.35, 0.65, m) * (1.0 - smoothstep(u_genP.x - 0.01, u_genP.x, d));
    return vec4(u_genColA, a);
  }
  if (u_genType == 6) {  // rings: p = (count, speed)
    float r = length(vec2((u - 0.5) * A, v - 0.5));
    float m = 0.5 + 0.5 * cos(FK_TAU * (r * u_genP.x - u_genTc * u_genP.y));
    return vec4(mix(u_genColA, u_genColB, m), 1.0);
  }
  if (u_genType == 7) {  // stars: p = (density, size, twinkle_hz, seed)
    float a = fkDots(vec2(u * A, v) * u_genP.x, u_genP.y, uint(u_genP.w), 0.55, u_genP.z, 0.0);
    return vec4(u_genColA, a);
  }
  if (u_genType == 8) {  // snow: p = (density, size, speed, drift), q.x = seed
    uint seed = uint(u_genQ.x);
    float x = u * A * u_genP.x;
    float col = fkRnd(fkHash(int(floor(x)) + 1048576, 0, seed ^ 0x5a5au), 0u);
    float y = v * u_genP.x - u_genTc * u_genP.z * u_genP.x * (0.7 + 0.6 * col) + 7.0 * col;
    return vec4(u_genColA, fkDots(vec2(x, y), u_genP.y, seed, 0.5, 0.0, u_genP.w));
  }
  if (u_genType == 9) {  // sweep: p = (width, centre, cos, sin)
    float p = u * u_genP.z + v * u_genP.w;
    return vec4(u_genColA, 1.0 - smoothstep(0.0, u_genP.x, abs(p - u_genP.y)));
  }
  if (u_genType == 10) {  // cycle: p = (cos, sin, spread, tc / period), q = (sat, brightness)
    float hue = (u * u_genP.x + v * u_genP.y) * u_genP.z + u_genP.w;
    vec3 f = fract(vec3(hue) + vec3(0.0, 2.0 / 3.0, 1.0 / 3.0));
    vec3 pure = clamp(abs(f * 6.0 - 3.0) - 1.0, 0.0, 1.0);
    return vec4(u_genQ.y * (1.0 - u_genQ.x + u_genQ.x * pure), 1.0);
  }
  if (u_genType == 11) {  // plasma: p = (scale, speed)
    float t = u_genTc * u_genP.y;
    float x = (u - 0.5) * A * u_genP.x;
    float y = (v - 0.5) * u_genP.x;
    float val = sin(x * 2.0 + t * 1.7) + sin(y * 2.3 - t * 1.3) + sin((x + y) * 1.4 + t)
      + sin(length(vec2(x, y)) * 2.6 - t * 2.1);
    return vec4(mix(u_genColA, u_genColB, 0.5 + 0.5 * sin(val * 1.2)), 1.0);
  }
  return vec4(u_genColA, u_genP.x);  // flicker: level computed on the CPU
}
`;
