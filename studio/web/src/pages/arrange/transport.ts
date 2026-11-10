import { useEffect, useState } from "react";

/** Minimal subset of HTMLAudioElement that Transport needs. */
export interface AudioLike {
  currentTime: number;
  ended?: boolean;
  play(): Promise<void> | void;
  pause(): void;
}

export interface TransportOptions {
  /** Monotonic clock in ms (performance.now). */
  now?: () => number;
  /** Current timeline duration in ms. */
  duration: () => number;
}

/** Audio currentTime updates coarsely; never interpolate further ahead than this. */
export const MAX_AUDIO_LEAD_MS = 100;

export class Transport {
  private now: () => number;
  private duration: () => number;
  private audio: AudioLike | null = null;
  private isPlaying = false;
  private baseT = 0;
  private baseNow = 0;
  private lastAudioT = 0;
  private lastAudioNow = 0;
  private tickCbs = new Set<(tMs: number) => void>();
  private changeCbs = new Set<() => void>();

  constructor(opts: TransportOptions) {
    this.now = opts.now ?? (() => performance.now());
    this.duration = opts.duration;
  }

  setAudio(audio: AudioLike | null): void {
    if (this.audio === audio) return;
    const t = this.tMs;
    if (this.audio && this.isPlaying) this.audio.pause();
    this.audio = audio;
    this.baseT = t;
    this.baseNow = this.now();
    if (audio) {
      audio.currentTime = t / 1000;
      this.lastAudioT = t;
      this.lastAudioNow = this.now();
      if (this.isPlaying) this.startAudio();
    }
  }

  get playing(): boolean {
    return this.isPlaying;
  }

  get tMs(): number {
    if (!this.isPlaying) return this.baseT;
    const raw = this.rawPosition();
    return Math.max(0, Math.min(raw, this.duration()));
  }

  private rawPosition(): number {
    const a = this.audio;
    if (a && !a.ended) {
      const at = a.currentTime * 1000;
      const now = this.now();
      if (at !== this.lastAudioT) {
        this.lastAudioT = at;
        this.lastAudioNow = now;
      }
      return at + Math.min(MAX_AUDIO_LEAD_MS, now - this.lastAudioNow);
    }
    return this.baseT + (this.now() - this.baseNow);
  }

  private startAudio(): void {
    const a = this.audio;
    if (!a) return;
    try {
      const r = a.play();
      if (r && typeof r.catch === "function") r.catch(() => {});
    } catch {
      // audio unavailable: keep playing on the clock
    }
  }

  play(): void {
    if (this.isPlaying) return;
    if (this.baseT >= this.duration()) this.baseT = 0;
    this.baseNow = this.now();
    this.isPlaying = true;
    if (this.audio) {
      this.audio.currentTime = this.baseT / 1000;
      this.lastAudioT = this.baseT;
      this.lastAudioNow = this.baseNow;
      this.startAudio();
    }
    this.emitChange();
    this.emitTick();
  }

  pause(): void {
    if (!this.isPlaying) return;
    this.baseT = this.tMs;
    this.isPlaying = false;
    this.audio?.pause();
    this.emitChange();
    this.emitTick();
  }

  seek(tMs: number): void {
    const t = Math.max(0, Math.min(tMs, this.duration()));
    this.baseT = t;
    this.baseNow = this.now();
    if (this.audio) {
      this.audio.currentTime = t / 1000;
      this.lastAudioT = t;
      this.lastAudioNow = this.baseNow;
    }
    this.emitChange();
    this.emitTick();
  }

  /** Advance (call once per animation frame while playing). Stops and rewinds at the end. */
  tick(): void {
    if (this.isPlaying && this.rawPosition() >= this.duration()) {
      this.isPlaying = false;
      this.audio?.pause();
      this.baseT = 0;
      if (this.audio) this.audio.currentTime = 0;
      this.emitChange();
    }
    this.emitTick();
  }

  /** Called on every tick, play, pause and seek. Returns an unsubscribe function. */
  onTick(cb: (tMs: number) => void): () => void {
    this.tickCbs.add(cb);
    return () => this.tickCbs.delete(cb);
  }

  /** Called only on play, pause, seek and end-of-show (not per frame). */
  onChange(cb: () => void): () => void {
    this.changeCbs.add(cb);
    return () => this.changeCbs.delete(cb);
  }

  private emitTick(): void {
    const t = this.tMs;
    for (const cb of [...this.tickCbs]) cb(t);
  }

  private emitChange(): void {
    for (const cb of [...this.changeCbs]) cb();
  }
}

/** Re-renders the caller on every transport tick; returns the current time in ms. */
export function useTransportTime(transport: Transport): number {
  const [t, setT] = useState(transport.tMs);
  useEffect(() => {
    setT(transport.tMs);
    return transport.onTick(setT);
  }, [transport]);
  return t;
}
