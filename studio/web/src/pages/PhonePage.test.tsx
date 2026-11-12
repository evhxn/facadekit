import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { COPY } from "./setup/cameraCopy";
import PhonePage from "./PhonePage";

const mocks = vi.hoisted(() => ({
  getProject: vi.fn(),
  startPhoneScan: vi.fn(),
  uploadPhoneVideo: vi.fn(),
  remotePair: vi.fn(),
}));

vi.mock("../api/client", async (orig) => ({
  ...(await orig<typeof import("../api/client")>()),
  ...mocks,
}));

import { ApiError } from "../api/client";

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/phone/prj_1"]}>
      <Routes>
        <Route path="/phone/:projectId" element={<PhonePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const video = () => new File([new Uint8Array([1])], "IMG_0001.MOV", { type: "video/quicktime" });

beforeEach(() => {
  sessionStorage.clear();
  mocks.getProject.mockResolvedValue({ id: "prj_1" });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PhonePage", () => {
  it("says to start the light show first without a session", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(COPY.phoneNoSession)).toBeTruthy());
    expect((screen.getByLabelText(COPY.phonePageSend) as HTMLInputElement).disabled).toBe(true);
  });

  it("starts the light show with a 20 s countdown and keeps the session", async () => {
    mocks.startPhoneScan.mockResolvedValue({ session_id: "phs_0123abcd", job_id: "job_1" });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: COPY.phonePageStart }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe(COPY.phonePageSwitch));
    expect(mocks.startPhoneScan).toHaveBeenCalledWith("prj_1", 20);
    expect(JSON.parse(sessionStorage.getItem("fk_phone_session")!)).toEqual({
      pid: "prj_1",
      sid: "phs_0123abcd",
    });
    expect(screen.queryByText(COPY.phoneNoSession)).toBeNull();
  });

  it("sends the video with the stored session", async () => {
    sessionStorage.setItem(
      "fk_phone_session",
      JSON.stringify({ pid: "prj_1", sid: "phs_0123abcd" }),
    );
    mocks.uploadPhoneVideo.mockImplementation(
      async (_p: string, _s: string, _f: File, onProgress: (f: number) => void) => {
        onProgress(0.5);
        return { job_id: "job_2" };
      },
    );
    renderPage();
    const input = (await screen.findByLabelText(COPY.phonePageSend)) as HTMLInputElement;
    expect(input.hasAttribute("capture")).toBe(false);
    const file = video();
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe(COPY.phonePageDone));
    expect(mocks.uploadPhoneVideo).toHaveBeenCalledWith(
      "prj_1",
      "phs_0123abcd",
      file,
      expect.any(Function),
      expect.any(AbortSignal),
    );
    expect(sessionStorage.getItem("fk_phone_session")).toBeNull();
  });

  it("ignores a session stored for another project", async () => {
    sessionStorage.setItem(
      "fk_phone_session",
      JSON.stringify({ pid: "prj_9", sid: "phs_0123abcd" }),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText(COPY.phoneNoSession)).toBeTruthy());
  });

  it("asks to pair when the project is forbidden", async () => {
    mocks.getProject.mockRejectedValueOnce(
      new ApiError("BAD_REQUEST", "Pair this device first", 403),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("Pair this phone")).toBeTruthy());
  });

  it("explains when this computer does not accept phone videos", async () => {
    mocks.startPhoneScan.mockRejectedValue(
      new ApiError("BAD_REQUEST", "Pair this device first", 403),
    );
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: COPY.phonePageStart }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(COPY.phonePageBlocked));
  });
});
