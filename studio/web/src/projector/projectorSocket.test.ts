import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { connectProjector } from "./projectorSocket";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  send(d: string) {
    this.sent.push(d);
  }
  close() {
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  message(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
  drop(code: number) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}

let rafQueue: (() => void)[] = [];
const tick = () => {
  const q = rafQueue;
  rafQueue = [];
  q.forEach((f) => f());
};

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.instances = [];
  rafQueue = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal("requestAnimationFrame", (f: () => void) => {
    rafQueue.push(f);
    return rafQueue.length;
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("connectProjector", () => {
  it("sends hello on open", () => {
    const conn = connectProjector({
      onOpen: () => conn.sendHello(1920, 1080, 1),
      onMessage: () => {},
    });
    FakeWebSocket.instances[0]!.open();
    expect(JSON.parse(FakeWebSocket.instances[0]!.sent[0]!)).toEqual({
      type: "hello",
      width: 1920,
      height: 1080,
      dpr: 1,
    });
  });

  it("acks after two animation frames", async () => {
    const onMessage = vi.fn();
    connectProjector({ onMessage });
    const ws = FakeWebSocket.instances[0]!;
    ws.open();
    ws.message({ type: "blank", seq: 18 });
    await vi.advanceTimersByTimeAsync(0);
    expect(onMessage).toHaveBeenCalledWith({ type: "blank", seq: 18 });
    expect(ws.sent).toHaveLength(0);
    tick();
    expect(ws.sent).toHaveLength(0);
    tick();
    await vi.advanceTimersByTimeAsync(0);
    expect(ws.sent.map((s) => JSON.parse(s))).toEqual([{ type: "ack", seq: 18 }]);
  });

  it("does not ack when the handler rejects", async () => {
    connectProjector({ onMessage: () => Promise.reject(new Error("bad image")) });
    const ws = FakeWebSocket.instances[0]!;
    ws.open();
    ws.message({ type: "image", seq: 1, url: "/x.png" });
    await vi.advanceTimersByTimeAsync(0);
    tick();
    tick();
    expect(ws.sent).toHaveLength(0);
  });

  it("does not reconnect after close code 4000", () => {
    const onTakenOver = vi.fn();
    connectProjector({ onMessage: () => {}, onTakenOver });
    FakeWebSocket.instances[0]!.drop(4000);
    vi.advanceTimersByTime(5000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(onTakenOver).toHaveBeenCalled();
  });

  it("reconnects after 1 s on other close codes", () => {
    connectProjector({ onMessage: () => {} });
    FakeWebSocket.instances[0]!.drop(1006);
    vi.advanceTimersByTime(999);
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });
});
