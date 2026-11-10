import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeProject } from "../api/client.test";
import type { Project } from "../api/types";
import { useProjectStore } from "./projectStore";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

describe("projectStore", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useProjectStore.setState({ project: makeProject(), dirty: false, saving: false, error: null });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("debounces two updates into one PUT and clears dirty", async () => {
    const fetchMock = vi.fn().mockImplementation((_u: string, init: RequestInit) => {
      const body = JSON.parse(init.body as string) as Project;
      return Promise.resolve(json({ ...body, revision: body.revision + 1 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const { update } = useProjectStore.getState();
    update((d) => {
      d.name = "A";
    });
    await vi.advanceTimersByTimeAsync(200);
    update((d) => {
      d.markers.push({ t_ms: 5, kind: "user" });
    });
    expect(useProjectStore.getState().dirty).toBe(true);
    await vi.advanceTimersByTimeAsync(600);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const sent = JSON.parse((fetchMock.mock.calls[0] as [string, RequestInit])[1].body as string);
    expect(sent.name).toBe("A");
    expect(sent.markers).toHaveLength(1);
    const s = useProjectStore.getState();
    expect(s.dirty).toBe(false);
    expect(s.project?.revision).toBe(2);
  });

  it("reloads and sets error on REVISION_CONFLICT", async () => {
    const fresh = makeProject({ name: "Server", revision: 7 });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ error: { code: "REVISION_CONFLICT", message: "stale" } }, 409))
      .mockResolvedValueOnce(json(fresh));
    vi.stubGlobal("fetch", fetchMock);
    useProjectStore.getState().update((d) => {
      d.name = "Mine";
    });
    await vi.advanceTimersByTimeAsync(600);
    const s = useProjectStore.getState();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(s.project?.name).toBe("Server");
    expect(s.dirty).toBe(false);
    expect(s.error).toContain("reloaded");
  });
});
