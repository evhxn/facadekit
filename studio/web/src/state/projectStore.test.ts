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
    useProjectStore.getState().replace(makeProject());
    useProjectStore.setState({ saving: false, error: null });
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

  describe("undo/redo", () => {
    const rename = (name: string) => (d: Project) => {
      d.name = name;
    };
    const name = () => useProjectStore.getState().project?.name;

    it("three updates, two undos, one redo gives the second state", () => {
      const s = useProjectStore.getState();
      s.update(rename("one"));
      s.update(rename("two"));
      s.update(rename("three"));
      s.undo();
      s.undo();
      expect(name()).toBe("one");
      s.redo();
      expect(name()).toBe("two");
      expect(useProjectStore.getState().canRedo).toBe(true);
      expect(useProjectStore.getState().canUndo).toBe(true);
    });

    it("coalesced updates form one undo step, but only within 500 ms", () => {
      const original = name();
      const s = useProjectStore.getState();
      s.update(rename("a"), { coalesceKey: "drag" });
      vi.advanceTimersByTime(100);
      s.update(rename("b"), { coalesceKey: "drag" });
      vi.advanceTimersByTime(100);
      s.update(rename("c"), { coalesceKey: "drag" });
      s.undo();
      expect(name()).toBe(original);
      expect(useProjectStore.getState().canUndo).toBe(false);
      s.redo();
      s.update(rename("d"), { coalesceKey: "x" });
      vi.advanceTimersByTime(600);
      s.update(rename("e"), { coalesceKey: "x" });
      s.undo();
      expect(name()).toBe("d");
    });

    it("undo keeps the current revision and marks the project dirty", () => {
      const s = useProjectStore.getState();
      s.update(rename("one"));
      useProjectStore.setState((st) => ({
        project: st.project ? { ...st.project, revision: 9 } : st.project,
      }));
      s.undo();
      expect(useProjectStore.getState().project?.revision).toBe(9);
      expect(useProjectStore.getState().dirty).toBe(true);
    });

    it("a new edit clears redo; load/replace clear the stacks", () => {
      const s = useProjectStore.getState();
      s.update(rename("one"));
      s.undo();
      s.update(rename("two"));
      expect(useProjectStore.getState().canRedo).toBe(false);
      s.replace(makeProject());
      expect(useProjectStore.getState().canUndo).toBe(false);
    });

    it("keeps at most 100 snapshots", () => {
      const s = useProjectStore.getState();
      for (let i = 0; i < 120; i++) s.update(rename(`n${i}`));
      for (let i = 0; i < 150; i++) useProjectStore.getState().undo();
      expect(name()).toBe("n19");
    });
  });
});
