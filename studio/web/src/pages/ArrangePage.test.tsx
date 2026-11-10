import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Asset, Project } from "../api/types";
import { useProjectStore } from "../state/projectStore";
import ArrangePage from "./ArrangePage";

const mocks = vi.hoisted(() => ({
  deleteAsset: vi.fn(),
  uploadAsset: vi.fn(),
  projectorShow: vi.fn(),
  projectorTransport: vi.fn(),
}));

vi.mock("../api/client", async (orig) => ({
  ...(await orig<typeof import("../api/client")>()),
  ...mocks,
}));

vi.mock("../gl/compositor", () => ({
  PreviewCompositor: class {
    setProject() {}
    render() {}
    dispose() {}
  },
}));

import { ApiError } from "../api/client";

const video: Asset = {
  id: "ast_v",
  kind: "video",
  filename: "v.mp4",
  original_name: "clip.mp4",
  width: 640,
  height: 360,
  duration_ms: 8000,
  fps: 30,
  has_audio: false,
  has_alpha: false,
};
const audio: Asset = {
  id: "ast_a",
  kind: "audio",
  filename: "a.mp3",
  original_name: "song.mp3",
  width: null,
  height: null,
  duration_ms: 90000,
  fps: null,
  has_audio: true,
  has_alpha: false,
};

function baseProject(): Project {
  return {
    schema_version: 1,
    id: "prj_1",
    name: "Test",
    revision: 1,
    projector: { width: 1920, height: 1080, fps: 30 },
    calibration_id: null,
    surfaces: [],
    assets: [video, audio],
    timeline: { duration_ms: 60000, audio_asset_id: null, tracks: [] },
    markers: [],
    fpp: { host: null, playlist_name: null },
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/p/prj_1/arrange"]}>
      <Routes>
        <Route path="/p/:projectId/arrange" element={<ArrangePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const project = () => useProjectStore.getState().project!;

describe("ArrangePage", () => {
  beforeEach(() => {
    Object.values(mocks).forEach((m) => m.mockReset().mockResolvedValue(undefined));
    useProjectStore.setState({
      project: baseProject(),
      dirty: false,
      saving: false,
      error: null,
      saveNow: vi.fn().mockResolvedValue(undefined),
    });
  });
  afterEach(cleanup);

  it("adds a clip with the default values", () => {
    renderPage();
    fireEvent.click(screen.getByText("Add clip"));
    fireEvent.click(screen.getByText("Add to timeline"));
    const tracks = project().timeline.tracks;
    expect(tracks).toHaveLength(1);
    expect(tracks[0]!.clips).toHaveLength(1);
    expect(tracks[0]!.clips[0]).toMatchObject({
      asset_id: "ast_v",
      generator: null,
      surface_id: "canvas",
      start_ms: 0,
      duration_ms: 60000,
      in_offset_ms: 0,
      loop: true,
      fit: "fill",
      opacity: 1,
      fade_in_ms: 0,
      fade_out_ms: 0,
      blend: "normal",
    });
  });

  it("stores seconds as integer milliseconds", () => {
    renderPage();
    fireEvent.click(screen.getByText("Add clip"));
    fireEvent.change(screen.getByLabelText("Duration (s)"), { target: { value: "12.5" } });
    fireEvent.click(screen.getByText("Add to timeline"));
    expect(project().timeline.tracks[0]!.clips[0]!.duration_ms).toBe(12500);
    fireEvent.change(screen.getByLabelText("Timeline duration (s)"), {
      target: { value: "12.5" },
    });
    expect(project().timeline.duration_ms).toBe(12500);
  });

  it("uses an audio asset as show audio and adopts its duration", () => {
    renderPage();
    fireEvent.click(screen.getByText("Use as show audio"));
    expect(project().timeline.audio_asset_id).toBe("ast_a");
    expect(project().timeline.duration_ms).toBe(90000);
  });

  it("shows the specified message when the asset is in use", async () => {
    mocks.deleteAsset.mockRejectedValue(new ApiError("ASSET_IN_USE", "in use", 409));
    renderPage();
    fireEvent.click(screen.getByLabelText("Delete clip.mp4"));
    await waitFor(() =>
      expect(screen.getByText("Remove the clips that use this file first.")).toBeTruthy(),
    );
    expect(project().assets).toHaveLength(2);
  });

  it("drops a media-library asset on the timeline to create a clip", () => {
    renderPage();
    const content = screen.getByTestId("new-layer-row").parentElement!;
    fireEvent.drop(content, {
      dataTransfer: { types: ["application/x-facadekit-asset"], getData: () => "ast_v" },
    });
    const clips = project().timeline.tracks[0]!.clips;
    expect(clips).toHaveLength(1);
    expect(clips[0]).toMatchObject({ asset_id: "ast_v", duration_ms: 8000, surface_id: "canvas" });
  });

  it("deletes the selected timeline clip with Delete and undoes it with Ctrl+Z", () => {
    renderPage();
    fireEvent.click(screen.getByText("Add clip"));
    fireEvent.click(screen.getByText("Add to timeline"));
    const bar = screen.getByRole("button", { name: "Clip clip.mp4" });
    fireEvent.keyDown(bar.closest("[aria-label='Timeline']")!, { key: "Delete" });
    expect(project().timeline.tracks.flatMap((t) => t.clips)).toHaveLength(0);
    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    expect(project().timeline.tracks.flatMap((t) => t.clips)).toHaveLength(1);
  });

  it("makes library cards draggable with the asset id", () => {
    renderPage();
    const card = screen.getByLabelText("Delete clip.mp4").closest("li")!;
    expect(card.getAttribute("draggable")).toBe("true");
    const setData = vi.fn();
    fireEvent.dragStart(card, { dataTransfer: { setData } });
    expect(setData).toHaveBeenCalledWith("application/x-facadekit-asset", "ast_v");
  });
});
