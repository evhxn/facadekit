import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, getProject, putProject, UPLOAD_TIMEOUT_MS, uploadPhoneVideo } from "./client";
import { userMessage } from "./errors";
import { newId, type Project } from "./types";

export function makeProject(over: Partial<Project> = {}): Project {
  return {
    schema_version: 1,
    id: "prj_1",
    name: "Test",
    revision: 1,
    projector: { width: 1920, height: 1080, fps: 30 },
    calibration_id: null,
    surfaces: [],
    assets: [],
    timeline: { duration_ms: 0, audio_asset_id: null, tracks: [] },
    markers: [],
    fpp: { host: null, playlist_name: null },
    ...over,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

afterEach(() => vi.unstubAllGlobals());

describe("client", () => {
  it("getProject parses a project", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(makeProject()));
    vi.stubGlobal("fetch", fetchMock);
    const p = await getProject("prj_1");
    expect(p.name).toBe("Test");
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/projects/prj_1");
  });

  it("throws ApiError with code and status on 409", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(json({ error: { code: "REVISION_CONFLICT", message: "stale" } }, 409)),
    );
    const err = await getProject("prj_1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("REVISION_CONFLICT");
    expect((err as ApiError).status).toBe(409);
  });

  it("maps a rejected fetch to NETWORK", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("down")));
    const err = await getProject("x").catch((e: unknown) => e);
    expect((err as ApiError).code).toBe("NETWORK");
  });

  it("putProject sends PUT with a JSON body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(makeProject({ revision: 2 })));
    vi.stubGlobal("fetch", fetchMock);
    await putProject(makeProject());
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/projects/prj_1");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string).id).toBe("prj_1");
  });
});

describe("helpers", () => {
  it("userMessage", () => {
    expect(userMessage("TOO_BRIGHT", "x")).toBe(
      "It is too bright to scan. Try again after sunset or turn off nearby lights.",
    );
    expect(userMessage("WHATEVER", "fallback")).toBe("fallback");
  });
  it("newId", () => {
    expect(newId("srf")).toMatch(/^srf_[0-9a-f]{8}$/);
  });
});

describe("phone video upload (XHR)", () => {
  class FakeXhr {
    static last: FakeXhr | null = null;
    timeout = 0;
    upload: { onprogress: ((e: ProgressEvent) => void) | null } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    ontimeout: (() => void) | null = null;
    method = "";
    url = "";
    sent: unknown = null;
    aborted = false;
    constructor() {
      FakeXhr.last = this;
    }
    open(method: string, url: string) {
      this.method = method;
      this.url = url;
    }
    setRequestHeader() {}
    send(body: unknown) {
      this.sent = body;
    }
    abort() {
      this.aborted = true;
      this.onabort?.();
    }
  }
  const original = globalThis.XMLHttpRequest;
  beforeEach(() => {
    globalThis.XMLHttpRequest = FakeXhr as unknown as typeof XMLHttpRequest;
  });
  afterEach(() => {
    globalThis.XMLHttpRequest = original;
  });

  it("posts to the session's video route with a timeout", async () => {
    const p = uploadPhoneVideo("prj 1", "phs_0123abcd", new File(["x"], "a.mov"));
    const xhr = FakeXhr.last!;
    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe("/api/v1/projects/prj%201/phone-scans/phs_0123abcd/video");
    expect(xhr.timeout).toBe(UPLOAD_TIMEOUT_MS);
    expect(xhr.sent).toBeInstanceOf(FormData);
    xhr.ontimeout?.();
    await expect(p).rejects.toMatchObject({ code: "NETWORK" });
  });

  it("aborts the request when the signal fires", async () => {
    const controller = new AbortController();
    const p = uploadPhoneVideo(
      "p",
      "phs_0123abcd",
      new File(["x"], "a.mov"),
      undefined,
      controller.signal,
    );
    controller.abort();
    expect(FakeXhr.last!.aborted).toBe(true);
    await expect(p).rejects.toMatchObject({ code: "NETWORK" });
  });
});
