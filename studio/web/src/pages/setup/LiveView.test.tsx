import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LiveView, { BACKOFF_MS, REFRESH_MS } from "./LiveView";

type Reply = { status: number; code?: string; message?: string };
let replies: Reply[];
let requests: string[];

function respond(reply: Reply): Response {
  const ok = reply.status === 200;
  return {
    ok,
    status: reply.status,
    headers: new Headers(),
    blob: async () => new Blob(["x"]),
    text: async () =>
      JSON.stringify({ error: { code: reply.code ?? "X", message: reply.message ?? "m" } }),
  } as unknown as Response;
}

beforeEach(() => {
  vi.useFakeTimers();
  requests = [];
  replies = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      requests.push(url);
      return respond(replies.shift() ?? { status: 200 });
    }),
  );
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:frame-${++n}`);
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const flush = () => act(async () => void (await vi.advanceTimersByTimeAsync(0)));
const advance = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));

function show(onGone = vi.fn(), paused = false) {
  return render(<LiveView cameraId="0" paused={paused} onFrame={vi.fn()} onGone={onGone} />);
}

describe("LiveView", () => {
  it("shows pictures one after the other", async () => {
    show();
    await flush();
    expect(screen.getByAltText("Live camera view").getAttribute("src")).toBe("blob:frame-1");
    await advance(REFRESH_MS);
    expect(screen.getByAltText("Live camera view").getAttribute("src")).toBe("blob:frame-2");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:frame-1");
    expect(requests[0]).toContain("/devices/cameras/0/snapshot.jpg");
  });

  it("stops asking for a camera the server does not know and reports it", async () => {
    // The first hardware run: a tab from a simulator session asked for camera "sim" once a
    // second for the whole evening.
    replies = [{ status: 404, code: "NOT_FOUND" }];
    const onGone = vi.fn();
    show(onGone);
    await flush();
    expect(onGone).toHaveBeenCalledOnce();
    await advance(60_000);
    expect(requests).toHaveLength(1);
  });

  it("waits longer after each failure, shows why, then stops until asked", async () => {
    const failure = {
      status: 500,
      code: "CAMERA_ACCESS_DENIED",
      message: "macOS is blocking the camera for Terminal.",
    };
    replies = Array.from({ length: 20 }, () => failure);
    show();
    await flush();
    expect(screen.getByRole("alert").textContent).toContain("macOS is blocking the camera");
    for (const [i, wait] of BACKOFF_MS.entries()) {
      await advance(wait - 1);
      expect(requests).toHaveLength(i + 1);
      await advance(1);
      expect(requests).toHaveLength(i + 2);
    }
    await advance(120_000);
    expect(requests).toHaveLength(BACKOFF_MS.length + 1);

    replies = [];
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await flush();
    expect(screen.getByAltText("Live camera view")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps waiting, without an error, while a scan holds the camera", async () => {
    replies = [{ status: 409, code: "BAD_REQUEST", message: "Camera is busy scanning" }];
    show();
    await flush();
    expect(screen.queryByRole("alert")).toBeNull();
    await advance(1000);
    expect(screen.getByAltText("Live camera view")).toBeTruthy();
  });

  it("asks for nothing while paused or after it is removed", async () => {
    const view = show(vi.fn(), true);
    await advance(5000);
    expect(requests).toHaveLength(0);
    view.rerender(<LiveView cameraId="0" paused={false} onFrame={vi.fn()} onGone={vi.fn()} />);
    await flush();
    expect(requests).toHaveLength(1);
    view.unmount();
    await advance(5000);
    expect(requests).toHaveLength(1);
  });
});
