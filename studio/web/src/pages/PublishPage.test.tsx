import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeProject } from "../api/client.test";
import type { Job, Project, RenderInfo } from "../api/types";
import { useProjectStore } from "../state/projectStore";
import PublishPage from "./PublishPage";

const api = vi.hoisted(() => ({
  startRender: vi.fn(),
  listRenders: vi.fn(),
  renderFileUrl: vi.fn((p: string, r: string) => `/r/${p}/${r}`),
  fppProbe: vi.fn(),
  fppStatus: vi.fn(),
  publish: vi.fn(),
  cancelJob: vi.fn(),
}));
const waitForJob = vi.hoisted(() => vi.fn());

vi.mock("../api/client", async (orig) => ({
  ...(await orig<typeof import("../api/client")>()),
  ...api,
}));
vi.mock("../api/events", () => ({ waitForJob, connectEvents: vi.fn(() => () => {}) }));

function job(over: Partial<Job> = {}): Job {
  return {
    id: "j1",
    kind: "render",
    state: "succeeded",
    progress: 1,
    message: "",
    error: null,
    result: null,
    created_at: "",
    ...over,
  };
}

const r1: RenderInfo = {
  render_id: "r1",
  filename: "old.mp4",
  size_bytes: 3 * 1048576,
  modified_at: "2026-01-01T00:00:00Z",
};
const r2: RenderInfo = { ...r1, render_id: "r2", filename: "new.mp4" };

function project(): Project {
  return makeProject({
    calibration_id: "cal_1",
    timeline: { duration_ms: 1000, audio_asset_id: "ast_a", tracks: [] },
  });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/p/prj_1/publish"]}>
      <Routes>
        <Route path="/p/:projectId/publish" element={<PublishPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const saveNow = vi.fn(() => Promise.resolve());

beforeEach(() => {
  vi.clearAllMocks();
  api.listRenders.mockResolvedValue([r1]);
  api.fppStatus.mockResolvedValue({
    status_name: "idle",
    current_playlist: null,
    seconds_played: null,
  });
  useProjectStore.setState({ project: project(), dirty: false, saveNow });
});
afterEach(cleanup);

async function connect(ok = true) {
  api.fppProbe.mockResolvedValue({
    reachable: true,
    version: "9.4",
    major: 9,
    ok,
    problems: ok ? [] : ["FPP 9.4 is too old…"],
  });
  fireEvent.change(screen.getByPlaceholderText("fpp.local"), {
    target: { value: "http://fpp.local/" },
  });
  fireEvent.click(screen.getByText("Connect"));
  await waitFor(() => expect(api.fppProbe).toHaveBeenCalledWith("fpp.local"));
}

describe("PublishPage", () => {
  it("renders the show, then refreshes and selects the new render", async () => {
    api.startRender.mockResolvedValue({ job_id: "j1" });
    waitForJob.mockResolvedValue(job({ result: { render_id: "r2" } }));
    renderPage();
    await screen.findByText("old.mp4");
    api.listRenders.mockResolvedValue([r2, r1]);
    fireEvent.click(screen.getByText("Render show"));
    await waitFor(() =>
      expect(api.startRender).toHaveBeenCalledWith("prj_1", {
        preset: "pi3-1080p30",
        include_audio: true,
        kind: "show",
      }),
    );
    expect(saveNow).toHaveBeenCalled();
    await waitFor(() => expect(api.listRenders).toHaveBeenCalledTimes(2));
    const radio = await screen.findByLabelText("Publish new.mp4");
    await waitFor(() => expect((radio as HTMLInputElement).checked).toBe(true));
  });

  it("shows an error banner for FFMPEG_FAILED", async () => {
    api.startRender.mockResolvedValue({ job_id: "j1" });
    waitForJob.mockResolvedValue(
      job({ state: "failed", error: { code: "FFMPEG_FAILED", message: "ffmpeg broke" } }),
    );
    renderPage();
    await screen.findByText("old.mp4");
    fireEvent.click(screen.getByText("Render show"));
    expect((await screen.findByRole("alert")).textContent).toContain("ffmpeg broke");
  });

  it("strips the scheme when probing and gates Send on ok", async () => {
    renderPage();
    await screen.findByText("old.mp4");
    await connect(false);
    expect(await screen.findByText("FPP 9.4 is too old…")).toBeTruthy();
    expect((screen.getByText("Send to FPP") as HTMLButtonElement).disabled).toBe(true);
    await connect(true);
    await screen.findByText("Connected to FPP 9.4");
    expect((screen.getByText("Send to FPP") as HTMLButtonElement).disabled).toBe(false);
    expect(useProjectStore.getState().project?.fpp.host).toBe("fpp.local");
  });

  it("publishes with and without a custom playlist name and shows the HDMI reminder", async () => {
    api.publish.mockResolvedValue({ job_id: "j2" });
    waitForJob.mockResolvedValue(
      job({ kind: "publish", result: { media_name: "m", playlist_name: "P", started: true } }),
    );
    renderPage();
    await screen.findByText("old.mp4");
    await connect();
    await screen.findByText("Connected to FPP 9.4");
    fireEvent.click(screen.getByText("Send to FPP"));
    await waitFor(() =>
      expect(api.publish).toHaveBeenCalledWith("prj_1", {
        host: "fpp.local",
        render_id: "r1",
        start: true,
      }),
    );
    await screen.findByText(/Move the projector's HDMI cable/);

    fireEvent.change(screen.getByLabelText(/Playlist name/), { target: { value: "Mine" } });
    fireEvent.click(screen.getByText("Send to FPP"));
    await waitFor(() =>
      expect(api.publish).toHaveBeenLastCalledWith("prj_1", {
        host: "fpp.local",
        render_id: "r1",
        start: true,
        playlist_name: "Mine",
      }),
    );
  });

  it("alignment check renders then publishes the returned render", async () => {
    api.startRender.mockResolvedValue({ job_id: "j1" });
    api.publish.mockResolvedValue({ job_id: "j2" });
    waitForJob
      .mockResolvedValueOnce(job({ result: { render_id: "ra" } }))
      .mockResolvedValueOnce(job({ kind: "publish", result: {} }));
    renderPage();
    await screen.findByText("old.mp4");
    await connect();
    await screen.findByText("Connected to FPP 9.4");
    fireEvent.click(screen.getByText("Render and send alignment check"));
    await waitFor(() =>
      expect(api.publish).toHaveBeenCalledWith("prj_1", {
        host: "fpp.local",
        render_id: "ra",
        start: true,
      }),
    );
    expect(api.startRender).toHaveBeenCalledWith("prj_1", {
      preset: "pi3-1080p30",
      include_audio: false,
      kind: "alignment_check",
    });
    await screen.findByText(/Look at the house/);
  });
});
