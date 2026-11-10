import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import * as events from "../api/events";
import type { Calibration, Job, Project } from "../api/types";
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
  cameraSnapshotUrl: vi.fn((cid: string) => `/snap/${cid}.jpg`),
  setCameraControls: vi.fn(),
  startCalibration: vi.fn(),
  getCalibration: vi.fn(),
  projViewUrl: vi.fn(() => "/projview.png"),
  measuredMaskUrl: vi.fn(() => "/mask.png"),
  cancelJob: vi.fn(),
  realign: vi.fn(),
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

describe("Step 2 - camera", () => {
  it("says so when there is no camera", async () => {
    m.listCameras.mockResolvedValue([]);
    render(<StepCamera cameraId={null} onCameraChange={() => {}} />);
    expect(await screen.findByText(/No camera found/)).toBeTruthy();
  });

  it("shows the snapshot for a single camera", async () => {
    m.listCameras.mockResolvedValue([{ id: "cam0", name: "Cam", width: 640, height: 480 }]);
    const onChange = vi.fn();
    const { rerender } = render(<StepCamera cameraId={null} onCameraChange={onChange} />);
    await waitFor(() => expect(onChange).toHaveBeenCalledWith("cam0"));
    rerender(<StepCamera cameraId="cam0" onCameraChange={onChange} />);
    const img = screen.getByAltText("Live camera view") as HTMLImageElement;
    expect(img.getAttribute("src")).toMatch(/^\/snap\/cam0\.jpg\?t=\d+$/);
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

  it("explains red areas for LOW_COVERAGE", async () => {
    m.startCalibration.mockResolvedValue({ job_id: "j1" });
    ev.waitForJob.mockResolvedValue(job({}));
    m.getProject.mockResolvedValue({ ...project, calibration_id: "c1" });
    m.getCalibration.mockResolvedValue(calibration(["LOW_COVERAGE"]));
    renderScan();
    fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
    expect(await screen.findByText(/could not see the red areas/)).toBeTruthy();
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
