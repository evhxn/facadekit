import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeProject } from "../api/client.test";
import type { Project } from "../api/types";
import { useProjectStore } from "../state/projectStore";
import SurfacesPage from "./SurfacesPage";

const { projectorShow, projectorStatus } = vi.hoisted(() => ({
  projectorShow: vi.fn(),
  projectorStatus: vi.fn(),
}));

vi.mock("../api/client", async (orig) => ({
  ...(await orig<typeof import("../api/client")>()),
  projectorShow,
  projectorStatus,
}));

import { ApiError } from "../api/client";

function projectWithSurface(): Project {
  return makeProject({
    calibration_id: "cal_1",
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
    timeline: {
      duration_ms: 1000,
      audio_asset_id: null,
      tracks: [
        {
          id: "trk_1",
          name: "T",
          clips: [
            {
              id: "clp_1",
              asset_id: null,
              generator: null,
              surface_id: "srf_a",
              start_ms: 0,
              duration_ms: 1000,
              in_offset_ms: 0,
              loop: false,
              fit: "fill",
              opacity: 1,
              fade_in_ms: 0,
              fade_out_ms: 0,
            },
          ],
        },
      ],
    } as unknown as Project["timeline"],
  });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/p/prj_1/surfaces"]}>
      <Routes>
        <Route path="/p/:projectId/surfaces" element={<SurfacesPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const quad = () => useProjectStore.getState().project!.surfaces[0]!.quad;

async function selectDoorCorner0() {
  fireEvent.click(await screen.findByText("Door"));
  const canvas = screen.getByLabelText("Surface canvas");
  fireEvent.keyDown(canvas, { key: "Tab" });
  return canvas;
}

describe("SurfacesPage", () => {
  beforeEach(() => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    projectorShow.mockReset().mockResolvedValue({});
    projectorStatus.mockReset().mockResolvedValue({ connected: false });
    useProjectStore.setState({
      project: projectWithSurface(),
      dirty: false,
      saving: false,
      error: null,
      saveNow: vi.fn().mockResolvedValue(undefined),
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("moves the selected corner with arrow keys", async () => {
    renderPage();
    const canvas = await selectDoorCorner0();
    fireEvent.keyDown(canvas, { key: "ArrowRight" });
    expect(quad()[0]).toEqual([101, 100]);
    fireEvent.keyDown(canvas, { key: "ArrowDown", shiftKey: true });
    expect(quad()[0]).toEqual([101, 110]);
  });

  it("flips horizontally", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("Door"));
    fireEvent.click(screen.getByText("Flip horizontally"));
    expect(quad()[0]).toEqual([300, 100]);
    expect(quad()[1]).toEqual([100, 100]);
  });

  it("deletes a used surface and its clips after confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    fireEvent.click(await screen.findByLabelText("Delete Door"));
    expect(confirm).toHaveBeenCalled();
    const p = useProjectStore.getState().project!;
    expect(p.surfaces).toHaveLength(0);
    expect(p.timeline.tracks[0]!.clips).toHaveLength(0);
  });

  it("keeps the surface when confirmation is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    fireEvent.click(await screen.findByLabelText("Delete Door"));
    expect(useProjectStore.getState().project!.surfaces).toHaveLength(1);
  });

  it("sends outlines to the projector after an edit", async () => {
    projectorStatus.mockResolvedValue({ connected: true });
    renderPage();
    const canvas = await selectDoorCorner0();
    await waitFor(() => expect(projectorShow).toHaveBeenCalled());
    projectorShow.mockClear();
    fireEvent.keyDown(canvas, { key: "ArrowRight" });
    await waitFor(() =>
      expect(projectorShow).toHaveBeenCalledWith({
        mode: "outlines",
        project_id: "prj_1",
        selected_id: "srf_a",
      }),
    );
  });

  it("turns the toggle off when the projector is not connected", async () => {
    projectorStatus.mockResolvedValue({ connected: true });
    projectorShow.mockRejectedValue(new ApiError("PROJECTOR_NOT_CONNECTED", "no", 409));
    renderPage();
    const toggle = (await screen.findByLabelText(/Show outlines on projector/)) as HTMLInputElement;
    await waitFor(() => expect(toggle.checked).toBe(true));
    await waitFor(() => expect(toggle.checked).toBe(false));
    expect(screen.getByText(/Projector window is not open/)).toBeTruthy();
    await act(async () => {});
  });
});
