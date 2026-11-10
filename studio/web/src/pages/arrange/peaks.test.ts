import { describe, expect, it } from "vitest";
import { computePeaks } from "./peaks";

describe("computePeaks", () => {
  it("a 1 s 1 kHz sine at 8 kHz gives 100 buckets spanning about +1 to -1", () => {
    const sr = 8000;
    const samples = new Float32Array(sr);
    for (let n = 0; n < sr; n++) samples[n] = Math.sin((2 * Math.PI * 1000 * n) / sr);
    const { min, max } = computePeaks(samples, sr, 100);
    expect(max.length).toBe(100);
    expect(min.length).toBe(100);
    for (let i = 0; i < 100; i++) {
      expect(max[i]).toBeCloseTo(1, 2);
      expect(min[i]).toBeCloseTo(-1, 2);
    }
  });

  it("silence gives all-zero buckets", () => {
    const { min, max } = computePeaks(new Float32Array(4000), 8000, 50);
    expect(max.every((v) => v === 0)).toBe(true);
    expect(min.every((v) => v === 0)).toBe(true);
    expect(max.length).toBe(25);
  });

  it("bucket count is ceil(durationSeconds * bucketsPerSecond)", () => {
    // 1.001 s at 8 kHz with 10 buckets/s -> ceil(10.01) = 11
    const { max } = computePeaks(new Float32Array(8008), 8000, 10);
    expect(max.length).toBe(11);
  });

  it("a ramp gives a non-decreasing max", () => {
    const sr = 8000;
    const samples = new Float32Array(sr * 2);
    for (let n = 0; n < samples.length; n++) samples[n] = n / samples.length;
    const { max } = computePeaks(samples, sr, 100);
    for (let i = 1; i < max.length; i++) expect(max[i]!).toBeGreaterThanOrEqual(max[i - 1]!);
    expect(max[max.length - 1]!).toBeGreaterThan(max[0]!);
  });
});
