import { describe, expect, it } from "vitest";
import type { Mask } from "../api/types";
import { maskBlurPx, maskCacheKey, maskTextureSize } from "./maskTexture";

const mask = (): Mask => ({
  outer: [
    [0, 0],
    [100, 0],
    [100, 80],
    [0, 80],
  ],
  holes: [
    [
      [20, 20],
      [40, 20],
      [30, 40],
    ],
  ],
});

describe("maskTexture pure parts", () => {
  it("uses half the projector size", () => {
    expect(maskTextureSize(1920, 1080)).toEqual([960, 540]);
    expect(maskTextureSize(481, 271)).toEqual([241, 136]);
  });

  it("blurs by feather_px / 4 on the half-size canvas", () => {
    expect(maskBlurPx(8)).toBe(2);
    expect(maskBlurPx(0)).toBe(0);
  });

  it("changes the cache key when a vertex or the feather changes, and not otherwise", () => {
    const base = maskCacheKey(mask(), 4, 480, 270);
    expect(maskCacheKey(mask(), 4, 480, 270)).toBe(base);
    const moved = mask();
    moved.outer[2] = [101, 80];
    expect(maskCacheKey(moved, 4, 480, 270)).not.toBe(base);
    const holeMoved = mask();
    holeMoved.holes[0]![1] = [41, 20];
    expect(maskCacheKey(holeMoved, 4, 480, 270)).not.toBe(base);
    expect(maskCacheKey(mask(), 5, 480, 270)).not.toBe(base);
    expect(maskCacheKey(mask(), 4, 960, 540)).not.toBe(base);
  });
});
