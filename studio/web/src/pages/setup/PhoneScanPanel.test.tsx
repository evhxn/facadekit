import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job } from "../../api/types";
import { COPY } from "./cameraCopy";
import PhoneScanPanel, { phoneErrorMessage, showText } from "./PhoneScanPanel";

const mocks = vi.hoisted(() => ({
  startPhoneScan: vi.fn(),
  uploadPhoneVideo: vi.fn(),
  remoteInfo: vi.fn(),
  cancelJob: vi.fn(),
  waitForJob: vi.fn(),
}));

vi.mock("../../api/client", async (orig) => ({
  ...(await orig<typeof import("../../api/client")>()),
  startPhoneScan: mocks.startPhoneScan,
  uploadPhoneVideo: mocks.uploadPhoneVideo,
  remoteInfo: mocks.remoteInfo,
  cancelJob: mocks.cancelJob,
}));
vi.mock("../../api/events", () => ({ waitForJob: mocks.waitForJob }));

import { ApiError } from "../../api/client";

function job(over: Partial<Job>): Job {
  return {
    id: "job_1",
    kind: "calibration",
    state: "running",
    progress: 0.1,
    message: "",
    error: null,
    result: null,
    created_at: "2026-11-12T00:00:00Z",
    ...over,
  };
}

const video = () =>
  new File([new Uint8Array([0, 0, 0, 24])], "IMG_0001.MOV", { type: "video/quicktime" });
const chooser = () => screen.getByLabelText(COPY.phoneChoose);

beforeEach(() => {
  sessionStorage.clear();
  mocks.cancelJob.mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PhoneScanPanel", () => {
  it("shows the four steps, the privacy note and a file input without capture", () => {
    render(<PhoneScanPanel projectId="prj_1" onDone={() => {}} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByText(COPY.phonePrivacy)).toBeTruthy();
    const input = chooser() as HTMLInputElement;
    expect(input.type).toBe("file");
    expect(input.accept).toBe("video/*");
    expect(input.hasAttribute("capture")).toBe(false);
    expect(screen.queryByText(COPY.phoneSendFromPhone)).toBeNull(); // owner decision pending
  });

  it("shows the countdown from the job progress message, then running, then done", async () => {
    mocks.startPhoneScan.mockResolvedValue({ session_id: "phs_0123abcd", job_id: "job_1" });
    let update: (j: Job) => void = () => {};
    let finish: (j: Job) => void = () => {};
    mocks.waitForJob.mockImplementation(
      (_id: string, onUpdate: (j: Job) => void) =>
        new Promise<Job>((resolve) => {
          update = onUpdate;
          finish = resolve;
        }),
    );
    render(<PhoneScanPanel projectId="prj_1" onDone={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: COPY.phoneStart }));
    await waitFor(() => expect(mocks.startPhoneScan).toHaveBeenCalledWith("prj_1"));
    act(() => update(job({ message: "Starting in 12 s" })));
    expect(screen.getByRole("status").textContent).toBe("Starting in 12 s");
    act(() => update(job({ message: "Light show 3 of 49" })));
    expect(screen.getByRole("status").textContent).toBe(COPY.phoneShowRunning);
    await act(async () =>
      finish(job({ state: "succeeded", result: { session_id: "phs_0123abcd" } })),
    );
    expect(screen.getByRole("status").textContent).toBe(COPY.phoneShowDone);
    expect(sessionStorage.getItem("fk_phone_session_prj_1")).toBe("phs_0123abcd");
  });

  it("asks for the light show first when there is no session", () => {
    render(<PhoneScanPanel projectId="prj_1" onDone={() => {}} />);
    fireEvent.change(chooser(), { target: { files: [video()] } });
    expect(screen.getByRole("alert").textContent).toBe(COPY.phoneNoSession);
    expect(mocks.uploadPhoneVideo).not.toHaveBeenCalled();
  });

  it("uploads with the session id, shows progress and reports the calibration", async () => {
    sessionStorage.setItem("fk_phone_session_prj_1", "phs_0123abcd");
    let progress: (f: number) => void = () => {};
    let sent: (v: { job_id: string }) => void = () => {};
    mocks.uploadPhoneVideo.mockImplementation(
      (_p: string, _s: string, _f: File, onProgress: (f: number) => void) =>
        new Promise((resolve) => {
          progress = onProgress;
          sent = resolve;
        }),
    );
    mocks.waitForJob.mockResolvedValue(
      job({ id: "job_2", state: "succeeded", result: { calibration_id: "cal_1" } }),
    );
    const onDone = vi.fn();
    render(<PhoneScanPanel projectId="prj_1" onDone={onDone} />);
    const file = video();
    fireEvent.change(chooser(), { target: { files: [file] } });
    await waitFor(() =>
      expect(mocks.uploadPhoneVideo).toHaveBeenCalledWith(
        "prj_1",
        "phs_0123abcd",
        file,
        expect.any(Function),
        expect.any(AbortSignal),
      ),
    );
    act(() => progress(0.42));
    const bar = screen.getByLabelText("Upload progress") as HTMLProgressElement;
    expect(bar.value).toBe(42);
    expect(screen.getByText(/42%/)).toBeTruthy();
    await act(async () => sent({ job_id: "job_2" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("cal_1"));
  });

  it("aborts the upload when the panel goes away", async () => {
    sessionStorage.setItem("fk_phone_session_prj_1", "phs_0123abcd");
    let signal: AbortSignal | undefined;
    mocks.uploadPhoneVideo.mockImplementation(
      (_p: string, _s: string, _f: File, _o: unknown, sig: AbortSignal) => {
        signal = sig;
        return new Promise(() => {});
      },
    );
    const view = render(<PhoneScanPanel projectId="prj_1" onDone={() => {}} />);
    fireEvent.change(chooser(), { target: { files: [video()] } });
    await waitFor(() => expect(signal).toBeDefined());
    expect(signal!.aborted).toBe(false);
    view.unmount();
    expect(signal!.aborted).toBe(true);
  });

  it("shows the phone error text when the analysis fails", async () => {
    sessionStorage.setItem("fk_phone_session_prj_1", "phs_0123abcd");
    mocks.uploadPhoneVideo.mockResolvedValue({ job_id: "job_2" });
    mocks.waitForJob.mockResolvedValue(
      job({ state: "failed", error: { code: "PHONE_VIDEO_NO_PATTERN", message: "x" } }),
    );
    render(<PhoneScanPanel projectId="prj_1" onDone={() => {}} />);
    fireEvent.change(chooser(), { target: { files: [video()] } });
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "does not contain the whole light show",
      ),
    );
  });

  it("forgets an expired session", async () => {
    sessionStorage.setItem("fk_phone_session_prj_1", "phs_0123abcd");
    mocks.uploadPhoneVideo.mockRejectedValue(
      new ApiError("NOT_FOUND", "This light show has expired. Start it again.", 404),
    );
    render(<PhoneScanPanel projectId="prj_1" onDone={() => {}} />);
    fireEvent.change(chooser(), { target: { files: [video()] } });
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe(
        "This light show has expired. Start it again.",
      ),
    );
    fireEvent.change(chooser(), { target: { files: [video()] } });
    expect(screen.getByText(COPY.phoneNoSession)).toBeTruthy();
    expect(mocks.uploadPhoneVideo).toHaveBeenCalledTimes(1);
  });

  it("offers the phone page with QR code and pairing code when enabled", async () => {
    mocks.remoteInfo.mockResolvedValue({
      enabled: true,
      code: "123456",
      urls: ["http://192.168.1.2:8137"],
    });
    render(<PhoneScanPanel projectId="prj_1" onDone={() => {}} phonePage />);
    fireEvent.click(screen.getByText(COPY.phoneSendFromPhone));
    await waitFor(() =>
      expect(screen.getByLabelText("QR code for http://192.168.1.2:8137/phone/prj_1")).toBeTruthy(),
    );
    expect(screen.getByText("123456")).toBeTruthy();
  });

  it("explains LAN mode when it is off", async () => {
    mocks.remoteInfo.mockResolvedValue({ enabled: false, code: null, urls: [] });
    render(<PhoneScanPanel projectId="prj_1" onDone={() => {}} phonePage />);
    await waitFor(() => expect(screen.getByText(COPY.phoneLanOff)).toBeTruthy());
  });
});

describe("helpers", () => {
  it("adds the phone hint to SCAN_FAILED only", () => {
    expect(phoneErrorMessage("SCAN_FAILED", "x")).toContain("Did the phone move?");
    expect(phoneErrorMessage("PHONE_MOVED", "x")).not.toContain("Did the phone move?");
  });
  it("picks the light show text", () => {
    expect(showText("running", null)).toBe(COPY.phoneShowStarting);
    expect(showText("running", job({ message: "Starting in 5 s" }))).toBe("Starting in 5 s");
    expect(showText("done", job({}))).toBe(COPY.phoneShowDone);
  });
});
