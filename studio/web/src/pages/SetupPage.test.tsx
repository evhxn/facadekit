import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import * as events from "../api/events";
import type { Calibration, CameraCheck, CameraRoi, Job, Project } from "../api/types";
import { useProjectStore } from "../state/projectStore";
import SetupPage from "./SetupPage";
import StepCamera from "./setup/StepCamera";
import StepScan from "./setup/StepScan";

vi.mock("../api/client", async (orig) => ({
  ...(await orig<typeof import("../api/client")>()),
  health: vi.fn(),
  getProject: vi.fn(),
  projectorStatus: vi.fn(),
  projectorShow: vi.fn(),
  listCameras: vi.fn(),
  cameraAccess: vi.fn(),
  fetchSnapshot: vi.fn(),
  cameraSnapshotUrl: vi.fn((cid: string) => `/snap/${cid}.jpg`),
  setCameraControls: vi.fn(),
  startCalibration: vi.fn(),
  startCameraCheck: vi.fn(),
  getCalibration: vi.fn(),
  sendScanFeedback: vi.fn(() => Promise.resolve({ recorded: true })),
  projViewUrl: vi.fn(() => "/projview.png"),
  measuredMaskUrl: vi.fn(() => "/mask.png"),
  cancelJob: vi.fn(),
  realign: vi.fn(),
  fppProbe: vi.fn(),
  startPhoneScan: vi.fn(),
  uploadPhoneVideo: vi.fn(),
}));
vi.mock("../api/events", () => ({
  connectEvents: vi.fn(() => () => {}),
  waitForJob: vi.fn(),
}));

const m = vi.mocked(client);
const ev = vi.mocked(events);

const project = {
  id: "p1",
  name: "Garage",
  revision: 1,
  projector: { width: 1920, height: 1080, fps: 30 },
  calibration_id: null,
} as unknown as Project;

function job(partial: Partial<Job>): Job {
  return {
    id: "j1",
    kind: "calibration",
    state: "succeeded",
    progress: 1,
    message: "",
    error: null,
    result: { calibration_id: "c1" },
    created_at: "",
    ...partial,
  };
}

function calibration(warnings: string[] = []): Calibration {
  return {
    id: "c1",
    mode: "scan",
    quality: { coverage: 0.92, planarity: 0, median_contrast: 0, lit_fraction: 0 },
    warnings,
  } as unknown as Calibration;
}

beforeEach(() => {
  useProjectStore.setState({ project, dirty: false });
  m.getProject.mockResolvedValue(project);
  m.health.mockResolvedValue({ status: "ok", version: "0", sim: false });
  m.cameraAccess.mockResolvedValue({ status: "authorized", message: null });
  m.fetchSnapshot.mockImplementation(async () => new Blob(["jpeg"]));
  let frames = 0;
  URL.createObjectURL = vi.fn(() => `blob:frame-${++frames}`);
  URL.revokeObjectURL = vi.fn();
  m.projectorShow.mockResolvedValue({
    connected: true,
    width: 1920,
    height: 1080,
    dpr: 1,
    mode: "grid",
  });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderSetup() {
  return render(
    <MemoryRouter initialEntries={["/p/p1/setup"]}>
      <Routes>
        <Route path="/p/:projectId/setup" element={<SetupPage />} />
        <Route path="/p/:projectId/surfaces" element={<div>surfaces route</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const sampleRoi: CameraRoi = {
  x: 320,
  y: 180,
  w: 640,
  h: 360,
  frame_width: 1280,
  frame_height: 720,
  bin: 1,
};

function sampleCheck(over: Partial<CameraCheck> = {}): CameraCheck {
  return {
    verdict: "too_coarse",
    reasons: ["SMALL_IN_FRAME"],
    camera_width: 1280,
    camera_height: 720,
    lit_box: sampleRoi,
    suggested_roi: sampleRoi,
    px_per_proj_px: 0.16,
    proj_px_per_cam_px: 6.4,
    stripe_modulation: {},
    snr: 10,
    median_contrast: 80,
    controls: {},
    suggest_1080p: false,
    ...over,
  };
}

const next = () => screen.getByRole("button", { name: "Next" }) as HTMLButtonElement;

describe("Step 1 - projector", () => {
  it("blocks Next while disconnected", async () => {
    m.projectorStatus.mockResolvedValue({
      connected: false,
      width: null,
      height: null,
      dpr: null,
      mode: "blank",
    });
    renderSetup();
    await screen.findByText("Projector not connected.");
    expect(next().disabled).toBe(true);
  });

  it("shows a mismatch and blocks Next", async () => {
    m.projectorStatus.mockResolvedValue({
      connected: true,
      width: 1280,
      height: 720,
      dpr: 1,
      mode: "blank",
    });
    renderSetup();
    expect(await screen.findByText(/1280×720, but this project needs 1920×1080/)).toBeTruthy();
    expect(next().disabled).toBe(true);
    expect(m.projectorShow).not.toHaveBeenCalled();
  });

  it("enables Next and shows the grid once when sizes match", async () => {
    m.projectorStatus.mockResolvedValue({
      connected: true,
      width: 1920,
      height: 1080,
      dpr: 1,
      mode: "blank",
    });
    renderSetup();
    expect(await screen.findByText(/white grid on the house/)).toBeTruthy();
    await waitFor(() => expect(next().disabled).toBe(false));
    expect(m.projectorShow).toHaveBeenCalledTimes(1);
    expect(m.projectorShow).toHaveBeenCalledWith({ mode: "grid" });
  });
});

describe("Step 1 - projector on the Raspberry Pi", () => {
  it("enables Next after a successful probe, without a projector window", async () => {
    m.projectorStatus.mockResolvedValue({
      connected: false,
      width: null,
      height: null,
      dpr: null,
      mode: "blank",
    });
    m.fppProbe.mockResolvedValue({
      reachable: true,
      version: "10.4",
      major: 10,
      ok: true,
      problems: [],
    });
    renderSetup();
    fireEvent.click(await screen.findByLabelText(/my Raspberry Pi/));
    expect(next().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("FPP address"), { target: { value: "fpp.local" } });
    fireEvent.click(screen.getByRole("button", { name: "Check connection" }));
    await waitFor(() => expect(next().disabled).toBe(false));
    expect(m.fppProbe).toHaveBeenCalledWith("fpp.local");
    expect(m.projectorShow).not.toHaveBeenCalled();
  });

  it("keeps Next disabled when the probe fails", async () => {
    m.projectorStatus.mockResolvedValue({
      connected: false,
      width: null,
      height: null,
      dpr: null,
      mode: "blank",
    });
    m.fppProbe.mockResolvedValue({
      reachable: false,
      version: null,
      major: null,
      ok: false,
      problems: ["Could not reach FPP at nope."],
    });
    renderSetup();
    fireEvent.click(await screen.findByLabelText(/my Raspberry Pi/));
    fireEvent.change(screen.getByLabelText("FPP address"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Check connection" }));
    expect(await screen.findByText(/Could not reach FPP at nope/)).toBeTruthy();
    expect(next().disabled).toBe(true);
  });
});

const noop = () => {};
function cameraProps(over: Partial<React.ComponentProps<typeof StepCamera>> = {}) {
  return {
    projectId: "p1",
    cameraId: null,
    onCameraChange: noop,
    roi: null,
    onRoiChange: noop,
    roiFromLastScan: false,
    check: null,
    onCheckChange: noop,
    onNext: noop,
    onUsePhone: noop,
    ...over,
  } as React.ComponentProps<typeof StepCamera>;
}

describe("Step 2 - camera", () => {
  it("says so when there is no camera", async () => {
    m.listCameras.mockResolvedValue([]);
    render(<StepCamera {...cameraProps()} />);
    expect(await screen.findByText(/No camera found/)).toBeTruthy();
  });

  it("shows the snapshot for a single camera", async () => {
    m.listCameras.mockResolvedValue([{ id: "cam0", name: "Cam", width: 640, height: 480 }]);
    const onChange = vi.fn();
    const { rerender } = render(<StepCamera {...cameraProps({ onCameraChange: onChange })} />);
    await waitFor(() => expect(onChange).toHaveBeenCalledWith("cam0"));
    rerender(<StepCamera {...cameraProps({ cameraId: "cam0", onCameraChange: onChange })} />);
    const img = (await screen.findByAltText("Live camera view")) as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("blob:frame-1");
    expect(m.fetchSnapshot.mock.calls[0]?.[0]).toBe("cam0");
  });

  it("names the camera, and says what kind it is when that matters", async () => {
    m.listCameras.mockResolvedValue([
      { id: "0", name: "MacBook Pro Camera", width: 1280, height: 720, kind: "built_in" },
      { id: "1", name: "Sam’s iPhone Camera", width: 1280, height: 720, kind: "phone" },
      { id: "2", name: "Logi C920", width: 1280, height: 720, kind: "external" },
    ]);
    render(<StepCamera {...cameraProps()} />);
    await screen.findByRole("option", { name: "MacBook Pro Camera (built in)" });
    expect(screen.getByRole("option", { name: "Sam’s iPhone Camera" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Logi C920" })).toBeTruthy();
  });

  it("shows why the system refuses the camera", async () => {
    m.listCameras.mockResolvedValue([{ id: "0", name: "Cam", width: 1280, height: 720 }]);
    m.cameraAccess.mockResolvedValue({
      status: "denied",
      message: "macOS is blocking the camera for Terminal. Open System Settings.",
    });
    render(<StepCamera {...cameraProps()} />);
    expect((await screen.findByRole("alert")).textContent).toContain("macOS is blocking");
  });

  it("drops a camera the server no longer knows and looks again", async () => {
    m.listCameras.mockResolvedValue([{ id: "0", name: "Cam", width: 1280, height: 720 }]);
    m.fetchSnapshot.mockRejectedValue(new client.ApiError("NOT_FOUND", "camera not found", 404));
    const onChange = vi.fn();
    render(<StepCamera {...cameraProps({ cameraId: "sim", onCameraChange: onChange })} />);
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(null));
    expect(m.fetchSnapshot).toHaveBeenCalledTimes(1);
    expect(m.listCameras.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("says so when the camera list cannot be loaded, and Refresh looks again", async () => {
    m.listCameras.mockRejectedValue(new client.ApiError("INTERNAL", "listing broke", 500));
    render(<StepCamera {...cameraProps()} />);
    expect((await screen.findByRole("alert")).textContent).toContain("listing broke");
    m.listCameras.mockResolvedValue([]);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText(/No camera found/)).toBeTruthy();
    expect(m.listCameras).toHaveBeenLastCalledWith(true);
  });
});

describe("Step 2 - camera check", () => {
  const cam = { id: "cam0", name: "Cam", width: 1280, height: 720 };
  beforeEach(() => {
    m.listCameras.mockResolvedValue([cam]);
  });

  it("runs the check with the current crop, then shows the result card", async () => {
    m.startCameraCheck.mockResolvedValue({ job_id: "jc" });
    let finish: (j: Job) => void = () => {};
    ev.waitForJob.mockReturnValue(new Promise<Job>((resolve) => (finish = resolve)));
    const onCheckChange = vi.fn();
    render(<StepCamera {...cameraProps({ cameraId: "cam0", roi: sampleRoi, onCheckChange })} />);
    fireEvent.click(screen.getByRole("button", { name: "Check the camera view" }));
    expect(
      await screen.findByText(
        "Checking… the projector will flash and show stripes for a few seconds.",
      ),
    ).toBeTruthy();
    expect(m.startCameraCheck).toHaveBeenCalledWith("p1", "cam0", sampleRoi);
    await act(async () => {
      finish(job({ kind: "analyze", result: { check: sampleCheck() } }));
    });
    expect(onCheckChange).toHaveBeenCalledWith(sampleCheck());
  });

  it("stops asking the camera for pictures while the check runs", async () => {
    vi.useFakeTimers();
    try {
      m.startCameraCheck.mockResolvedValue({ job_id: "jc" });
      ev.waitForJob.mockReturnValue(new Promise<Job>(() => {}));
      render(<StepCamera {...cameraProps({ cameraId: "cam0" })} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(m.fetchSnapshot.mock.calls.length).toBeGreaterThan(2); // live before the check
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Check the camera view" }));
      });
      const before = m.fetchSnapshot.mock.calls.length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(m.fetchSnapshot.mock.calls.length).toBe(before);
    } finally {
      vi.useRealTimers();
    }
  });

  it("suggests the crop: Crop to the lit area then Use this crop sends the suggestion up", () => {
    const onRoiChange = vi.fn();
    render(
      <StepCamera {...cameraProps({ cameraId: "cam0", check: sampleCheck(), onRoiChange })} />,
    );
    expect(screen.getByText(/about 6 projector pixels/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Crop to the lit area" }));
    expect(screen.getByText(/Only the inside of the box is used/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Use this crop" }));
    expect(onRoiChange).toHaveBeenLastCalledWith(sampleRoi);
  });

  it("sends an edited box up and shows the zoomed preview afterwards", () => {
    const onRoiChange = vi.fn();
    const props = cameraProps({
      cameraId: "cam0",
      check: sampleCheck(),
      roi: sampleRoi,
      onRoiChange,
    });
    const { rerender } = render(<StepCamera {...props} />);
    expect(m.cameraSnapshotUrl).toHaveBeenCalledWith("cam0", sampleRoi);
    expect(screen.getByAltText("Zoomed view of the cropped area")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Change the crop" }));
    const area = screen.getByTestId("crop-area");
    area.getBoundingClientRect = () => ({ width: 640, height: 360, left: 0, top: 0 }) as DOMRect;
    fireEvent(
      screen.getByRole("button", { name: /^Bottom right corner/ }),
      new MouseEvent("pointerdown", { bubbles: true, clientX: 480, clientY: 270 }),
    );
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: 544, clientY: 270 }));
      window.dispatchEvent(new MouseEvent("pointerup", { clientX: 544, clientY: 270 }));
    });
    fireEvent.click(screen.getByRole("button", { name: "Use this crop" }));
    const sent = onRoiChange.mock.calls.at(-1)?.[0] as CameraRoi;
    expect(Math.abs(sent.w - 768)).toBeLessThanOrEqual(2);
    rerender(<StepCamera {...props} roi={sent} />);
    expect(screen.getByAltText("Zoomed view of the cropped area")).toBeTruthy();
  });

  it("says where a crop came from and lets the user drop it", () => {
    const onRoiChange = vi.fn();
    render(
      <StepCamera
        {...cameraProps({ cameraId: "cam0", roi: sampleRoi, roiFromLastScan: true, onRoiChange })}
      />,
    );
    expect(screen.getByText("Using the crop from your last scan.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Use the whole picture" }));
    expect(onRoiChange).toHaveBeenCalledWith(null);
  });

  it("a failed check shows what to do and offers Check again", async () => {
    m.startCameraCheck.mockResolvedValue({ job_id: "jc" });
    ev.waitForJob.mockResolvedValue(
      job({
        state: "failed",
        error: { code: "CAMERA_NO_PROJECTION", message: "raw" },
        result: null,
      }),
    );
    render(<StepCamera {...cameraProps({ cameraId: "cam0" })} />);
    fireEvent.click(screen.getByRole("button", { name: "Check the camera view" }));
    const banner = await screen.findByRole("alert");
    expect(banner.textContent).toMatch(/Move the camera so the whole white rectangle/);
    expect(screen.getByRole("button", { name: "Check again" })).toBeTruthy();
  });

  it("an unexpected failure still ends with an action, and a size mismatch drops the crop", async () => {
    const onRoiChange = vi.fn();
    m.startCameraCheck.mockRejectedValue(new Error("boom"));
    render(<StepCamera {...cameraProps({ cameraId: "cam0", roi: sampleRoi, onRoiChange })} />);
    fireEvent.click(screen.getByRole("button", { name: "Check the camera view" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/press Check again/);

    m.startCameraCheck.mockRejectedValue(
      new client.ApiError(
        "BAD_REQUEST",
        "The crop was drawn for a different camera picture size. Check the camera again.",
        422,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toMatch(/different camera picture size/),
    );
    expect(onRoiChange).toHaveBeenCalledWith(null);
  });
});

describe("Step 3 - scan", () => {
  function renderScan() {
    return render(
      <MemoryRouter>
        <StepScan project={project} cameraId="cam0" />
      </MemoryRouter>,
    );
  }

  it("shows a friendly message and Retry on failure", async () => {
    m.startCalibration.mockResolvedValue({ job_id: "j1" });
    ev.waitForJob.mockResolvedValue(
      job({ state: "failed", error: { code: "TOO_BRIGHT", message: "raw" }, result: null }),
    );
    renderScan();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
    });
    expect(m.startCalibration).toHaveBeenCalledWith("p1", {
      mode: "scan",
      camera_id: "cam0",
      settings: { settle_ms: 0 },
    });
    expect(await screen.findByText(/too bright to scan/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("shows the projector view and coverage on success", async () => {
    m.startCalibration.mockResolvedValue({ job_id: "j1" });
    ev.waitForJob.mockResolvedValue(job({}));
    m.getProject.mockResolvedValue({ ...project, calibration_id: "c1" });
    m.getCalibration.mockResolvedValue(calibration());
    renderScan();
    fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
    expect(await screen.findByText("Measured 92% of the projection.")).toBeTruthy();
    expect(screen.getByAltText("Projector view of the house").getAttribute("src")).toBe(
      "/projview.png",
    );
  });

  it("sends scan feedback and names a learned strategy", async () => {
    m.startCalibration.mockResolvedValue({ job_id: "j1" });
    ev.waitForJob.mockResolvedValue(job({}));
    m.getProject.mockResolvedValue({ ...project, calibration_id: "c1" });
    m.getCalibration.mockResolvedValue({ ...calibration(), strategy: "soft_focus" });
    renderScan();
    fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
    expect(await screen.findByText(/used wider stripes for a soft/)).toBeTruthy();
    const cid = calibration().id;
    fireEvent.click(screen.getByRole("button", { name: "Picture looks wrong, try another way" }));
    expect(m.sendScanFeedback).toHaveBeenCalledWith("p1", cid, "bad");
    expect(m.startCalibration).toHaveBeenCalledTimes(2);
    expect(await screen.findByText(/used wider stripes for a soft/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Continue to surfaces" }));
    expect(m.sendScanFeedback).toHaveBeenLastCalledWith("p1", cid, "good");
  });

  it("explains red areas for LOW_COVERAGE", async () => {
    m.startCalibration.mockResolvedValue({ job_id: "j1" });
    ev.waitForJob.mockResolvedValue(job({}));
    m.getProject.mockResolvedValue({ ...project, calibration_id: "c1" });
    m.getCalibration.mockResolvedValue(calibration(["LOW_COVERAGE"]));
    renderScan();
    fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
    expect(await screen.findByText(/could not see the red areas/)).toBeTruthy();
  });

  it("starts a Pi-driven scan with the FPP host", async () => {
    m.startCalibration.mockResolvedValue({ job_id: "j1" });
    ev.waitForJob.mockResolvedValue(job({ state: "failed", error: null, result: null }));
    render(
      <MemoryRouter>
        <StepScan project={project} cameraId="cam0" piHost="fpp.local" />
      </MemoryRouter>,
    );
    expect(screen.getByText(/FPP will stop whatever it is playing/)).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
    });
    expect(m.startCalibration).toHaveBeenCalledWith("p1", {
      mode: "scan_pi",
      camera_id: "cam0",
      host: "fpp.local",
    });
  });

  it("sends the crop with a camera scan and leaves it out without one", async () => {
    m.startCalibration.mockResolvedValue({ job_id: "j1" });
    ev.waitForJob.mockResolvedValue(job({ state: "failed", error: null, result: null }));
    const { unmount } = render(
      <MemoryRouter>
        <StepScan project={project} cameraId="cam0" roi={sampleRoi} />
      </MemoryRouter>,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
    });
    expect(m.startCalibration).toHaveBeenLastCalledWith("p1", {
      mode: "scan",
      camera_id: "cam0",
      settings: { settle_ms: 0, roi: sampleRoi },
    });
    unmount();
    render(
      <MemoryRouter>
        <StepScan project={project} cameraId="cam0" roi={null} />
      </MemoryRouter>,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
    });
    const body = m.startCalibration.mock.calls.at(-1)?.[1];
    expect(body?.settings).toEqual({ settle_ms: 0 });
    expect(body?.settings && "roi" in body.settings).toBe(false);
  });

  it("does not send the crop on a Pi scan", async () => {
    m.startCalibration.mockResolvedValue({ job_id: "j1" });
    ev.waitForJob.mockResolvedValue(job({ state: "failed", error: null, result: null }));
    render(
      <MemoryRouter>
        <StepScan project={project} cameraId="cam0" piHost="fpp.local" roi={sampleRoi} />
      </MemoryRouter>,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
    });
    expect(m.startCalibration).toHaveBeenCalledWith("p1", {
      mode: "scan_pi",
      camera_id: "cam0",
      host: "fpp.local",
    });
  });

  it("drops a crop that was drawn for another picture size and says what to do", async () => {
    const onRoiReset = vi.fn();
    m.startCalibration.mockResolvedValue({ job_id: "j1" });
    ev.waitForJob.mockResolvedValue(
      job({
        state: "failed",
        error: {
          code: "BAD_REQUEST",
          message:
            "The crop was drawn for a different camera picture size. Check the camera again.",
        },
        result: null,
      }),
    );
    render(
      <MemoryRouter>
        <StepScan project={project} cameraId="cam0" roi={sampleRoi} onRoiReset={onRoiReset} />
      </MemoryRouter>,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
    });
    expect(await screen.findByText(/Check the camera again\./)).toBeTruthy();
    expect(onRoiReset).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("adds the small-picture line on the result screen for marginal and too coarse scans", async () => {
    m.getProject.mockResolvedValue({ ...project, calibration_id: "c1" });
    for (const verdict of ["marginal", "too_coarse", "good"] as const) {
      cleanup();
      m.getCalibration.mockResolvedValue({
        ...calibration(["CROP_TOO_TIGHT"]),
        quality: { coverage: 0.9, planarity: 0, median_contrast: 0, lit_fraction: 0, verdict },
      });
      render(
        <MemoryRouter>
          <StepScan project={{ ...project, calibration_id: "c1" }} cameraId="cam0" />
        </MemoryRouter>,
      );
      await screen.findByText("Measured 90% of the projection.");
      const line =
        "Your camera saw the projection small, so check the outlines carefully on the house.";
      expect(screen.queryByText(line) !== null).toBe(verdict !== "good");
      expect(screen.getByText(/Part of the lit area touched the edge of your crop/)).toBeTruthy();
    }
  });

  it("shows the phone panel when the phone was chosen", () => {
    render(
      <MemoryRouter>
        <StepScan project={project} cameraId="cam0" phone />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Scan with your phone" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start scan" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Use the camera instead" }));
    expect(screen.getByRole("button", { name: "Start scan" })).toBeTruthy();
  });

  it("opens the phone panel from the link under Start scan", () => {
    render(
      <MemoryRouter>
        <StepScan project={project} cameraId="cam0" />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Scan with your phone" }));
    expect(screen.getByRole("heading", { name: "Scan with your phone" })).toBeTruthy();
  });

  it("a phone scan ends on the same result screen as a webcam scan", async () => {
    sessionStorage.setItem("fk_phone_session_p1", "phs_0123abcd");
    m.uploadPhoneVideo.mockResolvedValue({ job_id: "j2" });
    ev.waitForJob.mockResolvedValue(
      job({ id: "j2", state: "succeeded", result: { calibration_id: "cal_1" } }),
    );
    m.getCalibration.mockResolvedValue(calibration());
    render(
      <MemoryRouter>
        <StepScan project={project} cameraId={null} phone />
      </MemoryRouter>,
    );
    const input = screen.getByLabelText("Choose video…");
    fireEvent.change(input, { target: { files: [new File([new Uint8Array([1])], "a.mov")] } });
    await screen.findByText("Measured 92% of the projection.");
    expect(m.uploadPhoneVideo).toHaveBeenCalledWith(
      "p1",
      "phs_0123abcd",
      expect.any(File),
      expect.any(Function),
      expect.any(AbortSignal),
    );
    expect(screen.getByRole("button", { name: "Continue to surfaces" })).toBeTruthy();
    // Scan again goes back to the phone panel, not to a webcam scan.
    fireEvent.click(screen.getByRole("button", { name: "Scan again" }));
    expect(screen.getByRole("heading", { name: "Scan with your phone" })).toBeTruthy();
    expect(m.startCalibration).not.toHaveBeenCalled();
    sessionStorage.clear();
  });

  it("skip scan starts manual mode", async () => {
    m.startCalibration.mockResolvedValue({ job_id: "j2" });
    ev.waitForJob.mockResolvedValue(job({ id: "j2", state: "failed", error: null }));
    renderScan();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Skip scan (manual mode)" }));
    });
    expect(m.startCalibration).toHaveBeenCalledWith("p1", { mode: "manual" });
  });
});

describe("Quick re-align", () => {
  const calibrated = {
    ...project,
    calibration_id: "c1",
    surfaces: [
      {
        id: "s1",
        name: "Door",
        quad: [
          [10, 10],
          [100, 10],
          [100, 80],
          [10, 80],
        ],
        mask: null,
        feather_px: 0,
        aspect: 1.3,
        mesh: null,
      },
    ],
  } as unknown as Project;

  function setupRealign(realignImpl: () => Promise<client.RealignResponse>) {
    useProjectStore.setState({ project: calibrated, dirty: false });
    m.getProject.mockResolvedValue(calibrated);
    m.getCalibration.mockResolvedValue(calibration());
    m.startCalibration.mockResolvedValue({ job_id: "j1" });
    ev.waitForJob.mockResolvedValue(job({ result: { calibration_id: "c2" } }));
    m.realign.mockImplementation(realignImpl);
  }

  const preview = {
    inliers: 100,
    reproj_error_px: 0.5,
    max_shift_px: 17.6,
    project: calibrated,
    warning: null,
  };

  it("shows the shift and applies", async () => {
    setupRealign(() => Promise.resolve(preview));
    renderSetup();
    fireEvent.click(await screen.findByRole("button", { name: /Projector moved/ }));
    expect(await screen.findByText(/moved by up to 18 px/)).toBeTruthy();
    expect(m.realign).toHaveBeenCalledWith("p1", {
      old_calibration_id: "c1",
      new_calibration_id: "c2",
      apply: false,
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await screen.findByText(/re-render the show/);
    expect(m.realign).toHaveBeenLastCalledWith("p1", {
      old_calibration_id: "c1",
      new_calibration_id: "c2",
      apply: true,
    });
  });

  it("shows no warning banner when the server sends none", async () => {
    setupRealign(() => Promise.resolve(preview));
    renderSetup();
    fireEvent.click(await screen.findByRole("button", { name: /Projector moved/ }));
    await screen.findByText(/moved by up to 18 px/);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows the warning with what to do next, and keeps Apply available", async () => {
    setupRealign(() =>
      Promise.resolve({
        ...preview,
        warning: "Surfaces moved by up to 90 px, much more than your usual re-align (6 px).",
      }),
    );
    renderSetup();
    fireEvent.click(await screen.findByRole("button", { name: /Projector moved/ }));
    const banner = await screen.findByRole("status");
    expect(banner.textContent).toMatch(/much more than your usual re-align/);
    expect(banner.textContent).toMatch(/place surfaces by hand/);
    expect(screen.getByRole("button", { name: "Apply" })).toBeTruthy();
  });

  it("hides Apply when the scans cannot be lined up", async () => {
    setupRealign(() =>
      Promise.reject(
        new client.ApiError("REALIGN_FAILED", "The two scans look too different.", 422),
      ),
    );
    renderSetup();
    fireEvent.click(await screen.findByRole("button", { name: /Projector moved/ }));
    expect(await screen.findByText(/too different/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
    expect(screen.getByRole("button", { name: /place surfaces by hand/ })).toBeTruthy();
  });
});

describe("Crop across the wizard", () => {
  const withLast = { ...project, calibration_id: "c1", surfaces: [] } as unknown as Project;

  it("starts from the crop of the last scan and sends it with the next scan", async () => {
    useProjectStore.setState({ project: withLast, dirty: false });
    m.getProject.mockResolvedValue(withLast);
    m.listCameras.mockResolvedValue([{ id: "cam0", name: "Cam", width: 1280, height: 720 }]);
    m.getCalibration.mockResolvedValue({
      ...calibration(),
      camera_id: "cam0",
      settings: { settle_ms: 300, roi: sampleRoi },
    } as unknown as Calibration);
    m.startCalibration.mockResolvedValue({ job_id: "j1" });
    ev.waitForJob.mockResolvedValue(job({ state: "failed", error: null, result: null }));
    renderSetup();
    fireEvent.click(await screen.findByRole("button", { name: "Back" })); // to the camera step
    expect(await screen.findByText("Using the crop from your last scan.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "Start scan" }));
    });
    expect(m.startCalibration).toHaveBeenCalledWith("p1", {
      mode: "scan",
      camera_id: "cam0",
      settings: { settle_ms: 0, roi: sampleRoi },
    });
  });

  it("ignores the last crop when it was made with another camera", async () => {
    useProjectStore.setState({ project: withLast, dirty: false });
    m.getProject.mockResolvedValue(withLast);
    m.listCameras.mockResolvedValue([{ id: "cam0", name: "Cam", width: 1280, height: 720 }]);
    m.getCalibration.mockResolvedValue({
      ...calibration(),
      camera_id: "other",
      settings: { settle_ms: 300, roi: sampleRoi },
    } as unknown as Calibration);
    renderSetup();
    fireEvent.click(await screen.findByRole("button", { name: "Back" }));
    await screen.findByAltText("Live camera view");
    await waitFor(() => expect(m.getCalibration).toHaveBeenCalled());
    expect(screen.queryByText("Using the crop from your last scan.")).toBeNull();
  });

  it("Use my phone instead goes to the scan step with the phone panel", async () => {
    useProjectStore.setState({ project, dirty: false });
    m.listCameras.mockResolvedValue([{ id: "cam0", name: "Cam", width: 1280, height: 720 }]);
    m.projectorStatus.mockResolvedValue({
      connected: true,
      width: 1920,
      height: 1080,
      dpr: 1,
      mode: "blank",
    });
    m.startCameraCheck.mockResolvedValue({ job_id: "jc" });
    ev.waitForJob.mockResolvedValue(job({ result: { check: sampleCheck() } }));
    renderSetup();
    await waitFor(() => expect(next().disabled).toBe(false));
    fireEvent.click(next());
    fireEvent.click(await screen.findByRole("button", { name: "Check the camera view" }));
    fireEvent.click(await screen.findByRole("button", { name: "Use my phone instead" }));
    expect(await screen.findByRole("heading", { name: "Scan with your phone" })).toBeTruthy();
    expect(screen.getByText("Step 3 of 3: Scan")).toBeTruthy();
  });

  it("re-align scans with the crop of the scan it compares against", async () => {
    const calibrated = {
      ...withLast,
      surfaces: [
        {
          id: "s1",
          name: "Door",
          quad: [
            [0, 0],
            [1, 0],
            [1, 1],
            [0, 1],
          ],
        },
      ],
    } as unknown as Project;
    useProjectStore.setState({ project: calibrated, dirty: false });
    m.getProject.mockResolvedValue(calibrated);
    m.getCalibration.mockResolvedValue({
      ...calibration(),
      camera_id: null,
      settings: { settle_ms: 300, roi: sampleRoi },
    } as unknown as Calibration);
    m.startCalibration.mockResolvedValue({ job_id: "j1" });
    ev.waitForJob.mockResolvedValue(job({ state: "failed", error: null, result: null }));
    renderSetup();
    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: /Projector moved/ }));
    });
    expect(m.startCalibration).toHaveBeenCalledWith("p1", {
      mode: "scan",
      settings: { roi: sampleRoi },
    });
  });
});
