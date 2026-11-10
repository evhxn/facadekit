import { PNG } from "pngjs";

export type Pt = [number, number];
export type Rgb = [number, number, number];

export interface SceneClip {
  asset: string;
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
  surfaces: Record<string, { quad: [Pt, Pt, Pt, Pt]; aspect: number }>;
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
];
