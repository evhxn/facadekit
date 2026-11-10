import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Project } from "../api/types";
import RemoteDialog from "../components/RemoteDialog";
import { useProjectStore } from "../state/projectStore";
import RemotePage from "./RemotePage";

const mocks = vi.hoisted(() => ({
  getProject: vi.fn(),
  putProject: vi.fn(),
  projectorShow: vi.fn(),
  remotePair: vi.fn(),
  remoteInfo: vi.fn(),
}));

vi.mock("../api/client", async (orig) => ({
  ...(await orig<typeof import("../api/client")>()),
  ...mocks,
}));
vi.mock("../api/events", () => ({ connectEvents: () => () => {} }));

import { ApiError } from "../api/client";

function project(): Project {
  return {
    schema_version: 1,
    id: "prj_1",
    name: "Test",
    revision: 1,
    projector: { width: 1920, height: 1080, fps: 30 },
    calibration_id: null,
    assets: [],
    timeline: { duration_ms: 0, audio_asset_id: null, tracks: [] },
    markers: [],
    fpp: { host: null, playlist_name: null },
    surfaces: [
      {
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
      },
    ],
  } as unknown as Project;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/remote/prj_1"]}>
      <Routes>
        <Route path="/remote/:projectId" element={<RemotePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const quad = () => useProjectStore.getState().project!.surfaces[0]!.quad;

beforeEach(() => {
  useProjectStore.setState({ project: null, dirty: false, saving: false, error: null });
  mocks.getProject.mockResolvedValue(project());
  mocks.putProject.mockImplementation(async (p: Project) => ({ ...p, revision: p.revision + 1 }));
  mocks.projectorShow.mockResolvedValue({ connected: true });
  mocks.remotePair.mockResolvedValue({ ok: true });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const press = (name: string) => {
  const b = screen.getByRole("button", { name });
  fireEvent.pointerDown(b);
  fireEvent.pointerUp(b);
};

describe("RemotePage", () => {
  it("nudges the selected corner and shows outlines", async () => {
    renderPage();
    await screen.findByText("Door");
    press("Right");
    expect(quad()[0]).toEqual([105, 100]);
    expect(quad()[1]).toEqual([300, 100]);
    await waitFor(() =>
      expect(mocks.projectorShow).toHaveBeenCalledWith({
        mode: "outlines",
        project_id: "prj_1",
        selected_id: "srf_a",
      }),
    );
    await screen.findByText("Saved");
  });

  it("moves all corners with All and respects the step", async () => {
    renderPage();
    await screen.findByText("Door");
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    fireEvent.click(screen.getByRole("button", { name: "20 px" }));
    press("Down");
    expect(quad().map((p) => p[1])).toEqual([120, 120, 220, 220]);
    expect(quad().map((p) => p[0])).toEqual([100, 300, 300, 100]);
  });

  it("repeats while an arrow is held", async () => {
    vi.useFakeTimers();
    try {
      renderPage();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      fireEvent.click(screen.getByRole("button", { name: "1 px" }));
      const b = screen.getByRole("button", { name: "Left" });
      fireEvent.pointerDown(b);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });
      fireEvent.pointerUp(b);
      expect(quad()[0]![0]).toBe(97);
    } finally {
      vi.useRealTimers();
    }
  });

  it("asks for the pairing code on 403, then loads", async () => {
    mocks.getProject
      .mockRejectedValueOnce(new ApiError("BAD_REQUEST", "Pair this device first", 403))
      .mockResolvedValue(project());
    renderPage();
    const input = await screen.findByLabelText("Pairing code");
    fireEvent.change(input, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Pair" }));
    await screen.findByText("Door");
    expect(mocks.remotePair).toHaveBeenCalledWith("123456");
  });
});

describe("RemoteDialog", () => {
  it("shows the restart instruction when LAN mode is off", async () => {
    mocks.remoteInfo.mockResolvedValue({ enabled: false, code: null, urls: [] });
    render(<RemoteDialog projectId="prj_1" onClose={() => {}} />);
    await screen.findByText(/facadekit serve --lan/);
  });

  it("shows URLs, a QR code and the pairing code when LAN mode is on", async () => {
    mocks.remoteInfo.mockResolvedValue({
      enabled: true,
      code: "123456",
      urls: ["http://192.168.1.20:8137"],
    });
    render(<RemoteDialog projectId="prj_1" onClose={() => {}} />);
    await screen.findByText("http://192.168.1.20:8137/remote/prj_1");
    expect(screen.getByText("123456")).toBeTruthy();
    expect(screen.getByRole("img").tagName.toLowerCase()).toBe("svg");
  });
});
