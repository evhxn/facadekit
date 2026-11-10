import { describe, expect, it } from "vitest";
import type { Generator } from "../api/types";
import { defaultGenerator, evalGenerator, parseColor } from "./generators";

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
