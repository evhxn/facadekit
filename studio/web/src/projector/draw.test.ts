import { describe, expect, it } from "vitest";
import { drawGrid, drawImage, drawOutlines } from "./draw";
import type { OutlinesMessage } from "./projectorSocket";

function mockCtx() {
  const calls: { fn: string; args: unknown[]; fill?: unknown; stroke?: unknown }[] = [];
  const ctx: Record<string, unknown> = { fillStyle: "", strokeStyle: "" };
  for (const fn of [
    "fillRect",
    "drawImage",
    "beginPath",
    "moveTo",
    "lineTo",
    "closePath",
    "stroke",
    "fill",
    "arc",
    "fillText",
  ]) {
    ctx[fn] = (...args: unknown[]) => {
      calls.push({ fn, args, fill: ctx.fillStyle, stroke: ctx.strokeStyle });
    };
  }
  return { ctx: ctx as unknown as CanvasRenderingContext2D, raw: ctx, calls };
}

describe("drawGrid", () => {
  it("draws 1px lines on whole pixels and border", () => {
    const { ctx, calls } = mockCtx();
    drawGrid(ctx, 240, 120);
    const rects = calls.filter((c) => c.fn === "fillRect" && c.fill === "#ffffff");
    const a = rects.map((c) => c.args);
    expect(a).toContainEqual([0, 0, 1, 120]);
    expect(a).toContainEqual([120, 0, 1, 120]);
    expect(a).toContainEqual([239, 0, 1, 120]);
    expect(a).toContainEqual([0, 0, 240, 1]);
    expect(a).toContainEqual([0, 119, 240, 1]);
    const red = calls.filter((c) => c.fn === "fillRect" && c.fill === "#ff0000");
    expect(red).toHaveLength(2);
  });
});

describe("drawOutlines", () => {
  it("scales points and colours the selected surface yellow", () => {
    const { ctx, calls } = mockCtx();
    const msg: OutlinesMessage = {
      type: "outlines",
      seq: 1,
      width: 1920,
      height: 1080,
      surfaces: [
        {
          id: "a",
          name: "A",
          quad: [
            [10, 10],
            [100, 10],
            [100, 80],
            [10, 80],
          ],
          selected: true,
        },
        {
          id: "b",
          name: "B",
          quad: [
            [200, 10],
            [300, 10],
            [300, 80],
            [200, 80],
          ],
          selected: false,
        },
      ],
    };
    drawOutlines(ctx, msg, 960, 540);
    const moves = calls.filter((c) => c.fn === "moveTo").map((c) => c.args);
    expect(moves[0]).toEqual([5, 5]);
    expect(moves[1]).toEqual([100, 5]);
    const lines = calls.filter((c) => c.fn === "lineTo").map((c) => c.args);
    expect(lines).toContainEqual([50, 5]);
    const strokes = calls.filter((c) => c.fn === "stroke");
    expect(strokes[0]?.stroke).toBe("#ffff00");
    expect(strokes[1]?.stroke).toBe("#ffffff");
  });
});

describe("drawImage", () => {
  it("disables smoothing and draws unscaled", () => {
    const { ctx, raw, calls } = mockCtx();
    const img = {} as CanvasImageSource;
    drawImage(ctx, img, 100, 100);
    expect(raw.imageSmoothingEnabled).toBe(false);
    const call = calls.find((c) => c.fn === "drawImage");
    expect(call?.args).toEqual([img, 0, 0]);
  });
});
