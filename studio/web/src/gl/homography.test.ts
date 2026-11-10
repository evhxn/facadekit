import { describe, expect, it } from "vitest";
import {
  applyH,
  fitRects,
  invert,
  multiply,
  quadHomography,
  type Mat3,
  type Pt,
} from "./homography";

describe("homography", () => {
  const q: Pt[] = [
    [512, 610.5],
    [1320.2, 598],
    [1335, 1012.4],
    [498.7, 1020],
  ];
  it("maps source corners to the quad", () => {
    const h = quadHomography(100, 50, q);
    const corners: Pt[] = [
      [0, 0],
      [100, 0],
      [100, 50],
      [0, 50],
    ];
    corners.forEach((c, i) => {
      const p = applyH(h, c);
      expect(p[0]).toBeCloseTo(q[i]![0], 6);
      expect(p[1]).toBeCloseTo(q[i]![1], 6);
    });
  });
  it("invert composes to identity", () => {
    const h = quadHomography(100, 50, q);
    const m = multiply(invert(h), h);
    const n = m.map((v) => v / m[8]);
    [1, 0, 0, 0, 1, 0, 0, 0, 1].forEach((v, i) => expect(n[i]).toBeCloseTo(v, 6));
  });
  it("applyH on identity returns input", () => {
    const id: Mat3 = [1, 0, 0, 0, 1, 0, 0, 0, 1];
    expect(applyH(id, [3, 4])).toEqual([3, 4]);
  });
  it("degenerate quad gives identity", () => {
    const z: Pt[] = [
      [1, 1],
      [1, 1],
      [1, 1],
      [1, 1],
    ];
    expect(quadHomography(1, 1, z)).toEqual([1, 0, 0, 0, 1, 0, 0, 0, 1]);
  });
});

describe("fitRects", () => {
  it("fill", () => {
    expect(fitRects(1920, 1080, 1, "fill")).toEqual({
      src: [420, 0, 1500, 1080],
      dst: [0, 0, 1, 1],
    });
  });
  it("fit", () => {
    expect(fitRects(1920, 1080, 1, "fit").dst).toEqual([0, 0.21875, 1, 0.78125]);
    expect(fitRects(100, 100, 2, "fit").dst).toEqual([0.25, 0, 0.75, 1]);
  });
  it("stretch", () => {
    expect(fitRects(10, 20, 3, "stretch")).toEqual({ src: [0, 0, 10, 20], dst: [0, 0, 1, 1] });
  });
});
