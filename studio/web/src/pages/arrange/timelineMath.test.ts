import { describe, expect, it } from "vitest";
import { msToX, rulerTicks, snap, xToMs } from "./timelineMath";

describe("timelineMath", () => {
  it("snap picks the nearest candidate within tolerance", () => {
    // 0.05 px/ms, tolerance 8 px = 160 ms
    expect(snap(1000, [800, 1100, 3000], 8, 0.05)).toBe(1100);
    expect(snap(1000, [1050, 1100], 8, 0.05)).toBe(1050);
  });
  it("snap returns the input when nothing is in range", () => {
    expect(snap(1000, [100, 3000], 8, 0.05)).toBe(1000);
    expect(snap(1000, [], 8, 0.05)).toBe(1000);
  });
  it("xToMs inverts msToX", () => {
    for (const ppm of [0.01, 0.05, 0.4]) {
      for (const t of [0, 1, 1234, 60000]) {
        expect(xToMs(msToX(t, ppm), ppm)).toBeCloseTo(t, 6);
      }
    }
  });
  it("rulerTicks spaces ticks and stays within the view", () => {
    const ticks = rulerTicks(0, 30000, 0.05); // 50 px/s -> 2 s step is 100 px? 1 s = 50 px < 70
    expect(ticks[0]).toEqual({ ms: 0, label: "0s" });
    expect(ticks[1]!.ms).toBe(2000);
    expect(ticks.every((t) => t.ms <= 30000)).toBe(true);
    expect(rulerTicks(5000, 9000, 0.05)[0]!.ms).toBe(4000);
  });
});
