import { PNG } from "pngjs";

export type Pt = [number, number];
export type Rgb = [number, number, number];

export interface SceneClip {
  asset?: string; // omitted for generator clips
  generator?: {
    type:
      | "solid"
      | "wash"
      | "outline"
      | "strobe"
      | "chase"
      | "rings"
      | "stars"
      | "snow"
      | "sweep"
      | "cycle"
      | "plasma"
      | "flicker";
    params: Record<string, number | string>;
  };
  surface: string; // key into scene.surfaces, or "canvas"
  track: number;
  fit: "fill" | "fit" | "stretch";
  opacity?: number;
  fade_in_ms?: number;
  blend?: "normal" | "add" | "screen";
}

export interface Scene {
  name: string;
  t_ms: number;
  surfaces: Record<
    string,
    {
      quad: [Pt, Pt, Pt, Pt];
      aspect: number;
      mask?: { outer: Pt[]; holes: Pt[][] };
      feather_px?: number;
      mesh?: { cols: number; rows: number; offsets: Pt[] };
    }
  >;
  assets: Record<string, Buffer>;
  clips: SceneClip[];
}

export const PROJECTOR = { width: 480, height: 270, fps: 30 };

function png(width: number, height: number, color: (x: number, y: number) => Rgb): Buffer {
  const img = new PNG({ width, height });
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const [r, g, b] = color(x, y);
      const i = (y * width + x) * 4;
      img.data[i] = r;
      img.data[i + 1] = g;
      img.data[i + 2] = b;
      img.data[i + 3] = 255;
    }
  }
  return PNG.sync.write(img);
}

const solid = (c: Rgb, w = 64, h = 64) => png(w, h, () => c);
const split = () => png(120, 120, (x) => (x < 60 ? [220, 40, 40] : [40, 60, 220]));
const checker = () =>
  png(240, 240, (x, y) =>
    (Math.floor(x / 60) + Math.floor(y / 60)) % 2 === 0 ? [240, 200, 40] : [20, 120, 140],
  );

const overlapSurfaces: Scene["surfaces"] = {
  a: {
    quad: [
      [60, 40],
      [300, 40],
      [300, 200],
      [60, 200],
    ],
    aspect: 1.5,
  },
  b: {
    quad: [
      [180, 90],
      [420, 90],
      [420, 240],
      [180, 240],
    ],
    aspect: 1.6,
  },
};

const blendScene = (name: string, blend: "add" | "screen"): Scene => ({
  name,
  t_ms: 1000,
  surfaces: overlapSurfaces,
  assets: { base: solid([200, 90, 40]), top: solid([40, 140, 200]) },
  clips: [
    { asset: "base", surface: "a", track: 0, fit: "stretch" },
    { asset: "top", surface: "b", track: 1, fit: "stretch", opacity: 0.6, blend },
  ],
});

/** A generator alone on a slanted wide quad (aspect 1.6), as on a garage door. */
const genScene = (
  name: string,
  t_ms: number,
  type: NonNullable<SceneClip["generator"]>["type"],
  params: Record<string, number | string>,
  blend: SceneClip["blend"] = "normal",
): Scene => ({
  name,
  t_ms,
  surfaces: {
    s: {
      quad: [
        [80, 35],
        [400, 50],
        [425, 240],
        [60, 225],
      ],
      aspect: 1.6,
    },
  },
  assets: {},
  clips: [{ generator: { type, params }, surface: "s", track: 0, fit: "fill", blend }],
});

export const scenes: Scene[] = [
  {
    name: "stretch-trapezoid",
    t_ms: 1000,
    surfaces: {
      s: {
        quad: [
          [120, 40],
          [400, 70],
          [440, 250],
          [60, 220],
        ],
        aspect: 1.5,
      },
    },
    assets: { checker: checker() },
    clips: [{ asset: "checker", surface: "s", track: 0, fit: "stretch" }],
  },
  {
    name: "fill-square",
    t_ms: 1000,
    surfaces: {
      s: {
        quad: [
          [140, 35],
          [340, 35],
          [340, 235],
          [140, 235],
        ],
        aspect: 1,
      },
    },
    assets: { split: split() },
    clips: [{ asset: "split", surface: "s", track: 0, fit: "fill" }],
  },
  {
    name: "fit-wide",
    t_ms: 1000,
    surfaces: {
      s: {
        quad: [
          [60, 45],
          [420, 45],
          [420, 225],
          [60, 225],
        ],
        aspect: 2,
      },
    },
    assets: { square: checker() },
    clips: [{ asset: "square", surface: "s", track: 0, fit: "fit" }],
  },
  blendScene("opacity-add", "add"),
  blendScene("screen", "screen"),
  {
    name: "fade",
    t_ms: 500,
    surfaces: {
      s: {
        quad: [
          [100, 50],
          [380, 50],
          [380, 220],
          [100, 220],
        ],
        aspect: 1.6,
      },
    },
    assets: { green: solid([60, 200, 90]) },
    clips: [{ asset: "green", surface: "s", track: 0, fit: "stretch", fade_in_ms: 1000 }],
  },
  {
    name: "canvas",
    t_ms: 1000,
    surfaces: {},
    assets: { split: split() },
    clips: [{ asset: "split", surface: "canvas", track: 0, fit: "fit" }],
  },
  {
    name: "mask-hole",
    t_ms: 1000,
    surfaces: {
      s: {
        quad: [
          [60, 30],
          [420, 30],
          [420, 240],
          [60, 240],
        ],
        aspect: 1.7,
        // Arched window with a rectangular cut-out, hard edges.
        mask: {
          outer: [
            [80, 230],
            [80, 110],
            [130, 60],
            [240, 40],
            [350, 60],
            [400, 110],
            [400, 230],
          ],
          holes: [
            [
              [180, 120],
              [300, 120],
              [300, 190],
              [180, 190],
            ],
          ],
        },
        feather_px: 0,
      },
    },
    assets: { checker: checker() },
    clips: [{ asset: "checker", surface: "s", track: 0, fit: "stretch" }],
  },
  {
    name: "gen-wash",
    t_ms: 1500,
    surfaces: {
      s: {
        quad: [
          [100, 40],
          [400, 60],
          [430, 240],
          [70, 215],
        ],
        aspect: 1.6,
      },
    },
    assets: {},
    clips: [
      {
        generator: {
          type: "wash",
          params: { color_a: "#ff8000", color_b: "#0040ff", period_s: 3, angle_deg: 30 },
        },
        surface: "s",
        track: 0,
        fit: "fill",
      },
    ],
  },
  {
    name: "gen-outline",
    t_ms: 1000,
    surfaces: {
      s: {
        quad: [
          [90, 30],
          [390, 45],
          [410, 235],
          [70, 220],
        ],
        aspect: 1.6,
      },
    },
    assets: {},
    clips: [
      {
        generator: {
          type: "outline",
          params: { color: "#40ff80", width: 0.05, speed: 0.5, tail: 0.5 },
        },
        surface: "s",
        track: 0,
        fit: "fill",
      },
    ],
  },
  {
    name: "mesh-bend",
    t_ms: 1000,
    surfaces: {
      s: {
        quad: [
          [90, 30],
          [400, 40],
          [420, 240],
          [70, 230],
        ],
        aspect: 1.5,
        // 4x4 cells, only the centre control point (row 2, column 2) moved by (25, -15).
        mesh: {
          cols: 4,
          rows: 4,
          offsets: Array.from({ length: 25 }, (_, k): Pt => (k === 12 ? [25, -15] : [0, 0])),
        },
      },
    },
    assets: { checker: checker() },
    clips: [{ asset: "checker", surface: "s", track: 0, fit: "stretch" }],
  },
  genScene("gen-chase", 1300, "chase", { color: "#ffd040", width: 0.06, dashes: 14, speed: 1.5 }),
  genScene("gen-rings", 1700, "rings", {
    color_a: "#ff6a00",
    color_b: "#2a0050",
    count: 3,
    speed: 0.4,
  }),
  genScene("gen-stars", 2300, "stars", {
    color: "#fff6d0",
    density: 9,
    size: 0.35,
    twinkle_hz: 0.6,
    seed: 4,
  }),
  genScene("gen-snow", 3100, "snow", {
    color: "#ffffff",
    density: 8,
    size: 0.35,
    speed: 0.2,
    drift: 0.5,
    seed: 11,
  }),
  genScene("gen-sweep", 1100, "sweep", {
    color: "#80c0ff",
    width: 0.2,
    period_s: 2.5,
    angle_deg: 30,
  }),
  genScene("gen-cycle", 2600, "cycle", {
    period_s: 6,
    spread: 0.8,
    angle_deg: 45,
    saturation: 0.9,
    brightness: 1,
  }),
  genScene("gen-plasma", 1900, "plasma", {
    color_a: "#ff00aa",
    color_b: "#00e0ff",
    scale: 3,
    speed: 0.7,
  }),
  genScene("gen-flicker", 1234, "flicker", { color: "#ffb040", rate_hz: 3, depth: 0.7, seed: 5 }),
  // Stars over a wash with "add": an effect layered on another, as the samples do.
  {
    ...genScene("gen-stars-over-wash", 900, "wash", {
      color_a: "#101040",
      color_b: "#301050",
      period_s: 5,
      angle_deg: 90,
    }),
    clips: [
      {
        generator: {
          type: "wash",
          params: { color_a: "#101040", color_b: "#301050", period_s: 5, angle_deg: 90 },
        },
        surface: "s",
        track: 0,
        fit: "fill",
      },
      {
        generator: {
          type: "stars",
          params: { color: "#ffffff", density: 10, size: 0.35, twinkle_hz: 0.5, seed: 8 },
        },
        surface: "s",
        track: 1,
        fit: "fill",
        blend: "add",
      },
    ],
  },
];
