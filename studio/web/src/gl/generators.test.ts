import { describe, expect, it } from "vitest";
import type { Generator } from "../api/types";
import fixture from "./generatorFixture.json";
import {
  GENERATOR_LABELS,
  GENERATOR_TYPES,
  SAFE_FLASH_HZ,
  cellHash,
  defaultGenerator,
  evalGenerator,
  flashWarning,
  flickerLevel,
  param,
  parseColor,
  rnd,
} from "./generators";

const gen = (type: Generator["type"], params: Generator["params"] = {}): Generator => ({
  type,
  params,
});
const bytes = (c: { r: number; g: number; b: number; a: number }) =>
  [c.r, c.g, c.b, c.a].map((x) => Math.round(x * 255));

describe("evalGenerator", () => {
  it("parses colours, falling back to white", () => {
    expect(parseColor("#ff8000")).toEqual([1, 128 / 255, 0]);
    expect(parseColor("red")).toEqual([1, 1, 1]);
  });

  it("solid is the colour with alpha 1", () => {
    expect(bytes(evalGenerator(gen("solid", { color: "#ff0000" }), 0.3, 0.7, 5))).toEqual([
      255, 0, 0, 255,
    ]);
  });

  it("wash follows the formula at tc = 0", () => {
    for (const x of [0, 255]) {
      const u = (x + 0.5) / 256;
      const m = 0.5 + 0.5 * Math.sin(2 * Math.PI * u);
      const c = evalGenerator(gen("wash"), u, 0.4, 0);
      expect(Math.abs(c.r * 255 - 255 * (1 - m))).toBeLessThan(0.01);
      expect(Math.abs(c.b * 255 - 255 * m)).toBeLessThan(0.01);
      expect(c.a).toBe(1);
    }
  });

  it("strobe at 1 Hz, duty 0.5", () => {
    const g = gen("strobe", { rate_hz: 1, duty: 0.5 });
    expect(evalGenerator(g, 0.5, 0.5, 0.25).a).toBe(1);
    expect(evalGenerator(g, 0.5, 0.5, 0.75).a).toBe(0);
  });

  it("outline: head at the top-left, tail behind it (clockwise from the corner)", () => {
    const g = gen("outline", { speed: 0.5 });
    // s ~ 0.99 on the left edge just below the corner: behind ~ 0.01 -> alpha ~ 1.
    expect(evalGenerator(g, 3.5 / 256, 8.5 / 256, 0).a).toBeGreaterThan(0.9);
    expect(evalGenerator(g, 8.5 / 256, 3.5 / 256, 0).a).toBe(0); // top edge, far tail end
    expect(evalGenerator(g, 0.5, 0.5, 0).a).toBe(0); // centre
    expect(evalGenerator(g, 0.5, 253 / 256, 0).a).toBe(0); // bottom edge, s ~ 0.625
    expect(evalGenerator(g, 253 / 256, 248 / 256, 1).a).toBeGreaterThan(0.9);
  });

  it("defaultGenerator fills the documented defaults", () => {
    expect(defaultGenerator("outline").params).toEqual({
      color: "#ffffff",
      width: 0.03,
      speed: 0.5,
      tail: 0.35,
    });
  });
});

describe("effects added after T-109", () => {
  it("hash agrees with the backend bit for bit", () => {
    const h = cellHash(1, 2, 3);
    expect(h).toBe(72785788);
    expect(rnd(h, 0)).toBe(4807689 / 16777216);
    const h2 = cellHash(-5, 7, 42);
    expect(h2).toBe(2305073543);
    expect(Math.abs(rnd(h2, 2) - 0.8092036843299866)).toBeLessThan(1e-12);
  });

  it("matches the backend renders in generatorFixture.json (within 1 level)", () => {
    for (const c of fixture as {
      type: Generator["type"];
      params: Generator["params"];
      tc: number;
      aspect: number;
      size: number;
      rgba: number[];
    }[]) {
      let worst = 0;
      for (let y = 0; y < c.size; y++) {
        for (let x = 0; x < c.size; x++) {
          const got = bytes(
            evalGenerator(
              gen(c.type, c.params),
              (x + 0.5) / c.size,
              (y + 0.5) / c.size,
              c.tc,
              c.aspect,
            ),
          );
          const i = (y * c.size + x) * 4;
          for (let k = 0; k < 4; k++) worst = Math.max(worst, Math.abs(got[k]! - c.rgba[i + k]!));
        }
      }
      expect(worst, `${c.type} at ${c.tc}`).toBeLessThanOrEqual(1);
    }
  });

  it("clamps numbers to the spec range", () => {
    expect(param(gen("flicker", { rate_hz: 40 }), "rate_hz")).toBe(SAFE_FLASH_HZ);
    expect(param(gen("stars", { density: "x" }), "density")).toBe(12);
  });

  it("flicker never changes direction more than 3 times a second", () => {
    const g = gen("flicker", { rate_hz: 20, depth: 1, seed: 9 });
    const levels = Array.from({ length: 300 }, (_, n) => flickerLevel(g, n / 30));
    let turns = 0;
    for (let i = 2; i < levels.length; i++) {
      const a = levels[i - 1]! - levels[i - 2]!;
      const b = levels[i]! - levels[i - 1]!;
      if (a * b < 0) turns++;
    }
    expect(turns).toBeLessThanOrEqual(30);
  });

  it("warns about strobe above 3 Hz only", () => {
    expect(flashWarning(gen("strobe", { rate_hz: 8 }))).toMatch(/seizures/);
    expect(flashWarning(gen("strobe", { rate_hz: 2 }))).toBeNull();
    expect(flashWarning(gen("flicker", { rate_hz: 3 }))).toBeNull();
  });

  it("every effect type has a label and defaults", () => {
    for (const t of GENERATOR_TYPES) {
      expect(GENERATOR_LABELS[t]).toBeTruthy();
      expect(Object.keys(defaultGenerator(t).params).length).toBeGreaterThan(0);
    }
    expect(GENERATOR_TYPES).toHaveLength(12);
  });
});
