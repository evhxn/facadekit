import { describe, expect, it } from "vitest";
import {
  clampPt,
  estimateAspect,
  flipHorizontal,
  hitTestCorner,
  isSelfIntersecting,
  makeView,
  moveQuad,
  moveQuadClamped,
  pointInQuad,
  rotateCorners,
  type Quad,
} from "./geometry";

const rect: Quad = [
  [0, 0],
  [200, 0],
  [200, 100],
  [0, 100],
];
const trapezoid: Quad = [
  [40, 0],
  [160, 0],
  [200, 100],
  [0, 100],
];

describe("geometry", () => {
  it("estimates aspect", () => {
    expect(estimateAspect(rect)).toBe(2);
  });

  it("pointInQuad on a trapezoid", () => {
    expect(pointInQuad([100, 50], trapezoid)).toBe(true);
    expect(pointInQuad([5, 10], trapezoid)).toBe(false);
    expect(pointInQuad([100, 150], trapezoid)).toBe(false);
    expect(pointInQuad([190, 90], trapezoid)).toBe(true);
  });

  it("hitTestCorner returns nearest corner or -1", () => {
    expect(hitTestCorner([198, 3], rect, 6)).toBe(1);
    expect(hitTestCorner([100, 50], rect, 6)).toBe(-1);
  });

  it("flips and rotates corners", () => {
    expect(flipHorizontal(rect)).toEqual([
      [200, 0],
      [0, 0],
      [0, 100],
      [200, 100],
    ]);
    expect(rotateCorners(rect)).toEqual([
      [200, 0],
      [200, 100],
      [0, 100],
      [0, 0],
    ]);
  });

  it("moves all points", () => {
    expect(moveQuad(rect, 3, -2)[2]).toEqual([203, 98]);
  });

  it("clamps points and quad moves", () => {
    expect(clampPt([-500, 5000], 1920, 1080)).toEqual([-200, 1280]);
    const moved = moveQuadClamped(rect, -1000, 0, 1920, 1080);
    expect(moved[0][0]).toBe(-200);
    expect(moved[1][0]).toBe(0);
  });

  it("detects bow-ties", () => {
    expect(isSelfIntersecting(rect)).toBe(false);
    expect(isSelfIntersecting([rect[1], rect[0], rect[2], rect[3]])).toBe(true);
  });

  it("view transforms round-trip", () => {
    for (const [zoom = 1, panX = 0, panY = 0] of [
      [1, 0, 0],
      [2.5, 40, -30],
      [8, -300, 120],
    ]) {
      const v = makeView({ imgW: 1920, imgH: 1080, viewW: 800, viewH: 600, zoom, panX, panY });
      for (const p of [
        [0, 0],
        [960, 540],
        [1919, 1079],
      ] as [number, number][]) {
        const q = v.toProjector(v.toScreen(p));
        expect(q[0]).toBeCloseTo(p[0], 6);
        expect(q[1]).toBeCloseTo(p[1], 6);
      }
    }
  });
});
