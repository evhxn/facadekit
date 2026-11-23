import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { Job, Surface, VerificationRecord } from "../../api/types";
import VerifyPanel, { isOutdated } from "./VerifyPanel";

const { startVerify, getVerification, cancelJob, waitForJob } = vi.hoisted(() => ({
  startVerify: vi.fn(),
  getVerification: vi.fn(),
  cancelJob: vi.fn(),
  waitForJob: vi.fn(),
}));
vi.mock("../../api/client", async (orig) => ({
  ...(await orig<typeof import("../../api/client")>()),
  startVerify,
  getVerification,
  cancelJob,
}));
vi.mock("../../api/events", () => ({ waitForJob }));

const surface: Surface = {
  id: "srf_a",
  name: "Door",
  quad: [
    [100, 100],
    [300, 100],
    [300, 200],
    [100, 200],
  ],
  mask: null,
  feather_px: 0,
  aspect: 2,
  mesh: null,
};

function record(over: Partial<VerificationRecord["surface"] & object> = {}): VerificationRecord {
  return {
    id: "vrf_1",
    created_at: "2026-11-22T00:00:00Z",
    calibration_id: "cal_1",
    camera_id: "sim",
    surface_id: "srf_a",
    surface_quad: surface.quad,
    surface_mask: null,
    level: 0.6,
    exposure_locked: true,
    shots: 5,
    duration_s: 2.1,
    overlay: true,
    injected: null,
    rig: {
      verdict: "stable",
      camera: "stable",
      projector: "stable",
      camera_shift_px: 0.1,
      projector_shift_px: 0.2,
      total_shift_px: 0.2,
      confidence: 0.9,
      dots_expected: 60,
      dots_found: 60,
      texture_matches: 80,
      texture_method: "features",
      drift: null,
      message: "Camera and projector have not moved since the scan.",
      recommendation: "Nothing to do.",
    },
    surface: {
      surface_id: "srf_a",
      verdict: "fail",
      iou_intended: 0.98,
      iou_object: 0.9,
      leakage: 0.06,
      uncovered: 0,
      max_edge_px: 9.5,
      object_confidence: 3,
      parallax_px: 0.3,
      inner_residual_px: 0.2,
      flatness_warning: false,
      outline_measured: 1,
      edges: [
        {
          side: "left",
          spill_px: 9.5,
          drift_px: 0,
          lit_px: 0.1,
          spill_range_px: [9.2, 9.5],
          samples: 20,
          advice: "Left edge spills 10 px past the object: pull it in.",
        },
      ],
      suggested_quad: [
        [110, 100],
        [300, 100],
        [300, 200],
        [110, 200],
      ],
      suggested_mask: null,
      suggestion_note: "",
      message: "The light misses the object by up to 10 px (left edge).",
      ...over,
    },
  };
}

function job(state: Job["state"], extra: Partial<Job> = {}): Job {
  return {
    id: "job_1",
    kind: "calibration",
    state,
    progress: 1,
    message: "",
    error: null,
    result: null,
    created_at: "",
    ...extra,
  };
}

beforeEach(() => {
  startVerify.mockReset();
  getVerification.mockReset().mockRejectedValue(new ApiError("NOT_FOUND", "none", 404));
  cancelJob.mockReset().mockResolvedValue(job("cancelled"));
  waitForJob.mockReset();
});
afterEach(cleanup);

const setup = (props: Partial<Parameters<typeof VerifyPanel>[0]> = {}) => {
  const onApply = vi.fn();
  const onBusy = vi.fn();
  render(
    <VerifyPanel
      projectId="prj_1"
      surface={surface}
      onApply={onApply}
      onBusy={onBusy}
      {...props}
    />,
  );
  return { onApply, onBusy };
};

describe("VerifyPanel", () => {
  it("shows the saved check with advice, numbers and the overlay", async () => {
    getVerification.mockResolvedValue(record());
    setup();
    expect(await screen.findByText(/Left edge spills 10 px/)).toBeTruthy();
    expect(screen.getByText("Off target")).toBeTruthy();
    expect(screen.getByText("6.0 %")).toBeTruthy();
    const img = screen.getByRole("img") as HTMLImageElement;
    expect(img.src).toContain("/verifications/srf_a/overlay.jpg?v=vrf_1");
    expect(screen.getByRole("button", { name: "Check again" })).toBeTruthy();
  });

  it("applies the suggested fix only when asked", async () => {
    getVerification.mockResolvedValue(record());
    const { onApply } = setup();
    const apply = await screen.findByRole("button", { name: "Apply suggested fix" });
    expect(onApply).not.toHaveBeenCalled();
    fireEvent.click(apply);
    expect(onApply).toHaveBeenCalledWith(record().surface?.suggested_quad, null);
    expect(screen.queryByRole("button", { name: "Apply suggested fix" })).toBeNull();
    expect(screen.getByText(/Applied/)).toBeTruthy();
  });

  it("runs a check, pauses the outlines meanwhile and shows the result", async () => {
    const prepare = vi.fn().mockResolvedValue(undefined);
    startVerify.mockResolvedValue({ job_id: "job_1" });
    waitForJob.mockImplementation(async (_id: string, onUpdate: (j: Job) => void) => {
      onUpdate(job("running", { progress: 0.5, message: "Lighting the surface" }));
      return job("succeeded", { result: { verification: record({ verdict: "pass" }) } });
    });
    const { onBusy } = setup({ prepare });
    fireEvent.click(screen.getByRole("button", { name: "Check on the house" }));
    expect(await screen.findByText("On target")).toBeTruthy();
    expect(prepare).toHaveBeenCalled();
    expect(startVerify).toHaveBeenCalledWith("prj_1", "srf_a");
    expect(onBusy.mock.calls).toEqual([[true], [false]]);
  });

  it("explains a failed check (camera access)", async () => {
    startVerify.mockResolvedValue({ job_id: "job_1" });
    waitForJob.mockResolvedValue(
      job("failed", {
        error: {
          code: "CAMERA_ACCESS_DENIED",
          message: "macOS is blocking the camera. Open System Settings.",
        },
      }),
    );
    const { onBusy } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Check on the house" }));
    expect(await screen.findByText(/macOS is blocking the camera/)).toBeTruthy();
    expect(onBusy).toHaveBeenLastCalledWith(false);
  });

  it("explains a check that could not start (no projector window)", async () => {
    startVerify.mockRejectedValue(
      new ApiError("PROJECTOR_NOT_CONNECTED", "no projector page is connected", 409),
    );
    const { onBusy } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Check on the house" }));
    expect(await screen.findByText(/projector window is not open/)).toBeTruthy();
    expect(onBusy).not.toHaveBeenCalled();
  });

  it("starts by itself after Magic select, once", async () => {
    startVerify.mockResolvedValue({ job_id: "job_1" });
    waitForJob.mockResolvedValue(job("succeeded", { result: { verification: record() } }));
    const onAutoStarted = vi.fn();
    setup({ autoStart: true, onAutoStarted });
    await waitFor(() => expect(startVerify).toHaveBeenCalledTimes(1));
    expect(onAutoStarted).toHaveBeenCalledTimes(1);
  });

  it("does not start in manual mode", () => {
    setup({ autoStart: true, disabledReason: "Checking needs a camera scan." });
    expect(startVerify).not.toHaveBeenCalled();
    const button = screen.getByRole("button", { name: "Check on the house" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("marks a check made for another outline as outdated and hides the fix", async () => {
    getVerification.mockResolvedValue({
      ...record(),
      surface_quad: [
        [0, 0],
        [1, 0],
        [1, 1],
        [0, 1],
      ],
    });
    setup();
    expect(await screen.findByText(/outline changed since this check/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Apply suggested fix" })).toBeNull();
  });

  it("isOutdated compares quad and mask", () => {
    expect(isOutdated(record(), surface)).toBe(false);
    expect(
      isOutdated(record(), {
        ...surface,
        mask: {
          outer: [
            [0, 0],
            [1, 0],
            [1, 1],
          ],
          holes: [],
        },
      }),
    ).toBe(true);
  });
});
