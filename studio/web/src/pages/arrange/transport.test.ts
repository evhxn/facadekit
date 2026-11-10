import { describe, expect, it, vi } from "vitest";
import { Transport, type AudioLike } from "./transport";

function setup(duration = 1000) {
  let now = 0;
  const t = new Transport({ now: () => now, duration: () => duration });
  return {
    t,
    advance(ms: number) {
      now += ms;
    },
  };
}

describe("Transport", () => {
  it("play advances, pause freezes, seek sets", () => {
    const { t, advance } = setup();
    t.play();
    advance(300);
    expect(t.tMs).toBe(300);
    t.pause();
    advance(500);
    expect(t.tMs).toBe(300);
    expect(t.playing).toBe(false);
    t.seek(700);
    expect(t.tMs).toBe(700);
  });

  it("stops and rewinds when reaching the duration", () => {
    const { t, advance } = setup(1000);
    const ticks: number[] = [];
    t.onTick((ms) => ticks.push(ms));
    t.play();
    advance(1200);
    t.tick();
    expect(t.playing).toBe(false);
    expect(t.tMs).toBe(0);
    expect(ticks[ticks.length - 1]).toBe(0);
  });

  it("uses audio currentTime as the clock, interpolating at most 100 ms", () => {
    const { t, advance } = setup(10000);
    const audio: AudioLike = { currentTime: 0, play: vi.fn(), pause: vi.fn() };
    t.setAudio(audio);
    t.play();
    audio.currentTime = 1;
    advance(40);
    expect(t.tMs).toBe(1000);
    advance(40);
    expect(t.tMs).toBe(1040);
    advance(500);
    expect(t.tMs).toBe(1100);
    t.pause();
    expect(audio.pause).toHaveBeenCalled();
  });
});
