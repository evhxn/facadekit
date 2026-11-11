import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CameraCheck, CameraRoi } from "../../api/types";
import CameraCheckCard from "./CameraCheckCard";
import { COPY } from "./cameraCopy";

afterEach(cleanup);

const roi: CameraRoi = {
  x: 320,
  y: 180,
  w: 640,
  h: 360,
  frame_width: 1280,
  frame_height: 720,
  bin: 1,
};

function makeCheck(over: Partial<CameraCheck> = {}): CameraCheck {
  return {
    verdict: "good",
    reasons: [],
    camera_width: 1280,
    camera_height: 720,
    lit_box: roi,
    suggested_roi: roi,
    px_per_proj_px: 0.9,
    proj_px_per_cam_px: 1.1,
    stripe_modulation: {},
    snr: 20,
    median_contrast: 100,
    controls: {},
    suggest_1080p: false,
    ...over,
  };
}

function setup(check: CameraCheck) {
  const handlers = { onCrop: vi.fn(), onUsePhone: vi.fn(), onNext: vi.fn(), onRecheck: vi.fn() };
  render(<CameraCheckCard check={check} {...handlers} />);
  return handlers;
}

const buttons = () => screen.getAllByRole("button").map((b) => b.textContent);
const primary = () => screen.getAllByRole("button").filter((b) => b.classList.contains("primary"));

describe("CameraCheckCard", () => {
  it("good: says so and offers Continue first", () => {
    const h = setup(makeCheck());
    expect(screen.getByText("Your camera sees the projection clearly.")).toBeTruthy();
    expect(screen.getByText(/Tip: crop to the lit area/)).toBeTruthy();
    expect(buttons()).toEqual(["Continue", "Crop to the lit area"]);
    expect(primary().map((b) => b.textContent)).toEqual(["Continue"]);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(h.onNext).toHaveBeenCalled();
  });

  it("good without a suggested crop has no crop button or tip", () => {
    setup(makeCheck({ suggested_roi: null }));
    expect(buttons()).toEqual(["Continue"]);
    expect(screen.queryByText(/Tip:/)).toBeNull();
  });

  it("marginal: crop is primary, then scan, then phone", () => {
    const h = setup(makeCheck({ verdict: "marginal", reasons: ["SMALL_IN_FRAME"] }));
    expect(screen.getByText(/sees the projection quite small/)).toBeTruthy();
    expect(buttons()).toEqual(["Crop to the lit area", "Scan with this camera", "Use my phone instead"]);
    expect(primary().map((b) => b.textContent)).toEqual(["Crop to the lit area"]);
    fireEvent.click(screen.getByRole("button", { name: "Crop to the lit area" }));
    expect(h.onCrop).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Scan with this camera" }));
    expect(h.onNext).toHaveBeenCalled();
  });

  it("marginal without a crop makes Scan with this camera primary", () => {
    setup(makeCheck({ verdict: "marginal", reasons: ["SMALL_IN_FRAME"], suggested_roi: null }));
    expect(primary().map((b) => b.textContent)).toEqual(["Scan with this camera"]);
  });

  it("too coarse: shows the pixel count and makes the phone primary", () => {
    const h = setup(
      makeCheck({ verdict: "too_coarse", reasons: ["SMALL_IN_FRAME"], proj_px_per_cam_px: 6.4 }),
    );
    expect(screen.getByText(/sees the projection very small/)).toBeTruthy();
    expect(screen.getByText(/about 6 projector pixels/)).toBeTruthy();
    expect(buttons()).toEqual(["Use my phone instead", "Crop to the lit area", "Scan anyway"]);
    expect(primary().map((b) => b.textContent)).toEqual(["Use my phone instead"]);
    fireEvent.click(screen.getByRole("button", { name: "Use my phone instead" }));
    expect(h.onUsePhone).toHaveBeenCalled();
  });

  it("blurry: asks to check again, whatever the verdict", () => {
    for (const verdict of ["marginal", "too_coarse"] as const) {
      cleanup();
      const h = setup(makeCheck({ verdict, reasons: ["BLURRY", "SMALL_IN_FRAME"] }));
      expect(screen.getByText(/The picture is soft/)).toBeTruthy();
      expect(buttons()).toEqual(["Check again", "Scan anyway"]);
      expect(primary().map((b) => b.textContent)).toEqual(["Check again"]);
      fireEvent.click(screen.getByRole("button", { name: "Check again" }));
      expect(h.onRecheck).toHaveBeenCalled();
    }
  });

  it("tells how to opt in to 1080p when the check suggests it", () => {
    setup(makeCheck({ verdict: "marginal", reasons: ["SMALL_IN_FRAME"], suggest_1080p: true }));
    expect(screen.getByText(/FACADEKIT_CAMERA_SIZE to 1920x1080, restart FacadeKit/)).toBeTruthy();
  });

  it("does not mention 1080p when it is not suggested", () => {
    setup(makeCheck({ verdict: "marginal", reasons: ["SMALL_IN_FRAME"], suggest_1080p: false }));
    expect(screen.queryByText(/1920x1080/)).toBeNull();
  });

  it("adds the grainy and very bright lines", () => {
    setup(makeCheck({ reasons: ["NOISY", "CLIPPED"] }));
    expect(screen.getByText(/The picture is grainy/)).toBeTruthy();
    expect(screen.getByText(/Parts of the picture are very bright/)).toBeTruthy();
  });
});

describe("cameraCopy", () => {
  it("has no technical words in anything the user can read", () => {
    const jargon = /\bROI\b|region|Gray|LSB|skip|homograph|modulation|SNR|verdict|bucket|strategy|decode/i;
    const strings: string[] = [];
    const walk = (v: unknown) => {
      if (typeof v === "string") strings.push(v);
      else if (typeof v === "function") strings.push(String((v as (...a: number[]) => unknown)(6, 4)));
      else if (v && typeof v === "object") Object.values(v).forEach(walk);
    };
    walk(COPY);
    expect(strings.length).toBeGreaterThan(20);
    for (const s of strings) expect(s, s).not.toMatch(jargon);
  });
});
