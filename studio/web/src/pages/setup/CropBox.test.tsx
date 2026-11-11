import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CameraRoi } from "../../api/types";
import CropBox from "./CropBox";

afterEach(cleanup);

const frame = { width: 1280, height: 720 };
const suggested: CameraRoi = {
  x: 320,
  y: 180,
  w: 640,
  h: 360,
  frame_width: 1280,
  frame_height: 720,
  bin: 1,
};

function setup(initial: CameraRoi = suggested) {
  const onUse = vi.fn();
  const onWhole = vi.fn();
  render(
    <CropBox frame={frame} initial={initial} suggested={suggested} active onUse={onUse} onWhole={onWhole}>
      <img alt="Live camera view" />
    </CropBox>,
  );
  // The picture is shown 640 x 360 on screen (half its natural size).
  const area = screen.getByTestId("crop-area");
  area.getBoundingClientRect = () =>
    ({ width: 640, height: 360, left: 0, top: 0, right: 640, bottom: 360, x: 0, y: 0 }) as DOMRect;
  return { onUse, onWhole };
}

const handle = (corner: string) =>
  screen.getByRole("button", { name: new RegExp(`^${corner} corner of the crop box`) });
const mouse = (type: string, x: number, y: number) =>
  new MouseEvent(type, { bubbles: true, clientX: x, clientY: y });

describe("CropBox", () => {
  it("shows the caption and the three buttons", () => {
    setup();
    expect(
      screen.getByText("Only the inside of the box is used. Keep the whole lit area inside the box."),
    ).toBeTruthy();
    for (const name of ["Use this crop", "Reset", "Use the whole picture"]) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
    expect(screen.getByText("The box is 640 by 360 pixels.")).toBeTruthy();
  });

  it("hands back the untouched suggestion as it is", () => {
    const { onUse } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Use this crop" }));
    expect(onUse).toHaveBeenCalledWith(suggested);
  });

  it("dragging the bottom-right handle by 10% of the shown width grows w by about 128 px", () => {
    const { onUse } = setup();
    const h = handle("Bottom right");
    fireEvent(h, mouse("pointerdown", 480, 270));
    act(() => {
      window.dispatchEvent(mouse("pointermove", 480 + 64, 270));
      window.dispatchEvent(mouse("pointerup", 544, 270));
    });
    fireEvent.click(screen.getByRole("button", { name: "Use this crop" }));
    const roi = onUse.mock.calls[0]![0] as CameraRoi;
    expect(Math.abs(roi.w - (640 + 128))).toBeLessThanOrEqual(2);
    expect(roi.x).toBe(320);
    expect(roi.h).toBe(360);
    expect(roi.w % 2).toBe(0);
    expect(roi.x + roi.w).toBeLessThanOrEqual(1280);
    expect(roi.frame_width).toBe(1280);
    expect(roi.bin).toBe(1);
  });

  it("clamps to the picture and keeps at least 64 x 64", () => {
    const { onUse } = setup();
    fireEvent(handle("Bottom right"), mouse("pointerdown", 480, 270));
    act(() => {
      window.dispatchEvent(mouse("pointermove", 5000, 5000));
      window.dispatchEvent(mouse("pointerup", 5000, 5000));
    });
    fireEvent(handle("Top left"), mouse("pointerdown", 160, 90));
    act(() => {
      window.dispatchEvent(mouse("pointermove", 5000, 5000));
      window.dispatchEvent(mouse("pointerup", 5000, 5000));
    });
    fireEvent.click(screen.getByRole("button", { name: "Use this crop" }));
    const roi = onUse.mock.calls[0]![0] as CameraRoi;
    expect(roi.x + roi.w).toBe(1280);
    expect(roi.y + roi.h).toBe(720);
    expect(roi.w).toBe(64);
    expect(roi.h).toBe(64);
  });

  it("rounds odd edges outwards to even numbers", () => {
    const { onUse } = setup({ ...suggested, x: 321, y: 181, w: 639, h: 359 });
    // A tiny move of the top-left corner by one shown pixel (= 2 camera pixels).
    fireEvent(handle("Top left"), mouse("pointerdown", 160, 90));
    act(() => {
      window.dispatchEvent(mouse("pointermove", 161, 91));
      window.dispatchEvent(mouse("pointerup", 161, 91));
    });
    fireEvent.click(screen.getByRole("button", { name: "Use this crop" }));
    const roi = onUse.mock.calls[0]![0] as CameraRoi;
    expect(roi.x % 2).toBe(0);
    expect(roi.y % 2).toBe(0);
  });

  it("moves a corner with the arrow keys: 1% of the picture, 5% with Shift", () => {
    const { onUse } = setup();
    const h = handle("Bottom right");
    h.focus();
    fireEvent.keyDown(h, { key: "ArrowRight" });
    fireEvent.keyDown(h, { key: "ArrowRight", shiftKey: true });
    fireEvent.click(screen.getByRole("button", { name: "Use this crop" }));
    const roi = onUse.mock.calls[0]![0] as CameraRoi;
    // 12.8 + 64 = 76.8 camera pixels, rounded outwards to even numbers
    expect(roi.w).toBeGreaterThanOrEqual(640 + 76);
    expect(roi.w).toBeLessThanOrEqual(640 + 80);
    expect(roi.w % 2).toBe(0);
  });

  it("Reset goes back to the suggestion and Use the whole picture gives null", () => {
    const { onUse, onWhole } = setup({ ...suggested, w: 400 });
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(screen.getByText("The box is 640 by 360 pixels.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Use this crop" }));
    expect(onUse).toHaveBeenCalledWith(suggested);
    fireEvent.click(screen.getByRole("button", { name: "Use the whole picture" }));
    expect(onWhole).toHaveBeenCalledTimes(1);
  });

  it("puts all four corner handles in the tab order", () => {
    setup();
    const names = ["Top left", "Top right", "Bottom left", "Bottom right"];
    for (const n of names) expect((handle(n) as HTMLButtonElement).tabIndex).toBe(0);
  });
});
