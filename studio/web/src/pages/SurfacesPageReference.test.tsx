import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeProject } from "../api/client.test";
import type { Calibration } from "../api/types";
import { useProjectStore } from "../state/projectStore";
import SurfacesPage from "./SurfacesPage";

const mocks = vi.hoisted(() => ({
  projectorShow: vi.fn(),
  projectorStatus: vi.fn(),
  getCalibration: vi.fn(),
  getReferencePhoto: vi.fn(),
  uploadReferencePhoto: vi.fn(),
  deleteReferencePhoto: vi.fn(),
}));

vi.mock("../api/client", async (orig) => ({
  ...(await orig<typeof import("../api/client")>()),
  ...mocks,
}));

import { ApiError } from "../api/client";

const info = { width: 4000, height: 3000, size_bytes: 123456, updated_at: "2026-11-23T10:00:00Z" };

function calibration(mode: "manual" | "scan"): Calibration {
  return { id: "cal_1", mode } as unknown as Calibration;
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

const loadedSources: string[] = [];

describe("Surfaces page: reference photo in manual mode", () => {
  beforeEach(() => {
    loadedSources.length = 0;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    vi.stubGlobal(
      "Image",
      class {
        onload: (() => void) | null = null;
        set src(value: string) {
          loadedSources.push(value);
        }
      },
    );
    mocks.projectorShow.mockReset().mockResolvedValue({});
    mocks.projectorStatus.mockReset().mockResolvedValue({ connected: false });
    mocks.getCalibration.mockReset().mockResolvedValue(calibration("manual"));
    mocks.getReferencePhoto.mockReset().mockResolvedValue(null);
    mocks.uploadReferencePhoto.mockReset();
    mocks.deleteReferencePhoto.mockReset().mockResolvedValue(undefined);
    useProjectStore.setState({
      project: makeProject({ calibration_id: "cal_1" }),
      dirty: false,
      saving: false,
      error: null,
      saveNow: vi.fn().mockResolvedValue(undefined),
    });
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("offers the photo, labelled by eye and not measured, and shows the blank view without one", async () => {
    renderPage();
    expect(await screen.findByText("Reference photo (by eye, not measured)")).toBeTruthy();
    expect(screen.getByText(/does not measure this photo/)).toBeTruthy();
    expect(screen.getByLabelText("Add a reference photo")).toBeTruthy();
    expect(screen.queryByRole("note")).toBeNull();
    expect(loadedSources.every((s) => s.endsWith("/proj_view.png"))).toBe(true);
  });

  it("is not offered after a camera scan", async () => {
    mocks.getCalibration.mockResolvedValue(calibration("scan"));
    renderPage();
    await waitFor(() => expect(mocks.getCalibration).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.queryByText("Reference photo (by eye, not measured)")).toBeNull();
    expect(mocks.getReferencePhoto).not.toHaveBeenCalled();
  });

  it("uploads a photo and puts it behind the surfaces with a note", async () => {
    mocks.uploadReferencePhoto.mockResolvedValue(info);
    renderPage();
    const input = (await screen.findByLabelText("Add a reference photo")) as HTMLInputElement;
    expect(input.accept).toBe("image/jpeg,image/png,image/webp");
    const file = new File([new Uint8Array([1, 2, 3])], "IMG_0001.JPG", { type: "image/jpeg" });
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
    expect(mocks.uploadReferencePhoto).toHaveBeenCalledWith("prj_1", file);
    expect((await screen.findByRole("note")).textContent).toMatch(/by eye, not measured/);
    expect(
      loadedSources.some((s) => s.includes("/reference-photo/view.jpg?v=2026-11-23T10%3A00%3A00Z")),
    ).toBe(true);
    expect(screen.getByLabelText("Replace the reference photo")).toBeTruthy();
  });

  it("shows an existing photo when the page opens", async () => {
    mocks.getReferencePhoto.mockResolvedValue(info);
    renderPage();
    expect((await screen.findByRole("note")).textContent).toMatch(/not measured/);
    expect(screen.getByText(/Photo in use \(4000 × 3000\)/)).toBeTruthy();
  });

  it("shows the server's reason when the file is not an image", async () => {
    mocks.uploadReferencePhoto.mockRejectedValue(
      new ApiError("BAD_REQUEST", "This file is not a photo FacadeKit can read.", 422),
    );
    renderPage();
    const input = await screen.findByLabelText("Add a reference photo");
    const file = new File(["x"], "notes.jpg", { type: "image/jpeg" });
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /not a photo FacadeKit can read/,
    );
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("refuses a non-image or oversized file before sending it", async () => {
    renderPage();
    const input = await screen.findByLabelText("Add a reference photo");
    const text = new File(["hello"], "a.txt", { type: "text/plain" });
    await act(async () => {
      fireEvent.change(input, { target: { files: [text] } });
    });
    expect((await screen.findByRole("alert")).textContent).toMatch(/not a picture/);
    const big = new File(["x"], "big.jpg", { type: "image/jpeg" });
    Object.defineProperty(big, "size", { value: 26 * 1024 * 1024 });
    await act(async () => {
      fireEvent.change(input, { target: { files: [big] } });
    });
    expect((await screen.findByRole("alert")).textContent).toMatch(/larger than 25 MB/);
    expect(mocks.uploadReferencePhoto).not.toHaveBeenCalled();
  });

  it("removes the photo and goes back to the blank view", async () => {
    mocks.getReferencePhoto.mockResolvedValue(info);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Remove photo" }));
    await waitFor(() => expect(screen.queryByRole("note")).toBeNull());
    expect(mocks.deleteReferencePhoto).toHaveBeenCalledWith("prj_1");
    expect(loadedSources[loadedSources.length - 1]).toMatch(/\/proj_view\.png$/);
  });
});
