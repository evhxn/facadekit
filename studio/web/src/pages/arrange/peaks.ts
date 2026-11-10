export interface Peaks {
  min: Float32Array;
  max: Float32Array;
  durationMs: number;
  bucketsPerSecond: number;
}

export interface PeakBuckets {
  min: Float32Array;
  max: Float32Array;
}

/**
 * Per-bucket min and max of a mono signal. Bucket i covers the samples whose
 * time falls in [i / bucketsPerSecond, (i + 1) / bucketsPerSecond).
 * The bucket count is ceil(durationSeconds * bucketsPerSecond).
 */
export function computePeaks(
  samples: Float32Array,
  sampleRate: number,
  bucketsPerSecond: number,
): PeakBuckets {
  const durationSeconds = samples.length / sampleRate;
  const n = Math.ceil(durationSeconds * bucketsPerSecond);
  const min = new Float32Array(n);
  const max = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const start = Math.floor((i * sampleRate) / bucketsPerSecond);
    const end = Math.min(
      samples.length,
      Math.max(start + 1, Math.floor(((i + 1) * sampleRate) / bucketsPerSecond)),
    );
    let lo = Infinity;
    let hi = -Infinity;
    for (let s = start; s < end; s++) {
      const v = samples[s]!;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    min[i] = lo === Infinity ? 0 : lo;
    max[i] = hi === -Infinity ? 0 : hi;
  }
  return { min, max };
}

const cache = new Map<string, Promise<Peaks>>();

/** Fetch and decode an audio file, then reduce it to peaks. Results are cached per URL. */
export function loadPeaks(url: string, bucketsPerSecond = 200): Promise<Peaks> {
  const hit = cache.get(url);
  if (hit) return hit;
  const pending = decodePeaks(url, bucketsPerSecond);
  cache.set(url, pending);
  // A failed load must not stay cached, so a later view can retry it.
  pending.catch(() => cache.delete(url));
  return pending;
}

async function decodePeaks(url: string, bucketsPerSecond: number): Promise<Peaks> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Audio fetch failed: ${res.status}`);
  const data = await res.arrayBuffer();
  const ctx = new AudioContext();
  try {
    const buf = await ctx.decodeAudioData(data);
    const mono = new Float32Array(buf.length);
    const channels = buf.numberOfChannels;
    for (let c = 0; c < channels; c++) {
      const ch = buf.getChannelData(c);
      for (let i = 0; i < ch.length; i++) mono[i]! += ch[i]! / channels;
    }
    const { min, max } = computePeaks(mono, buf.sampleRate, bucketsPerSecond);
    return { min, max, durationMs: Math.round(buf.duration * 1000), bucketsPerSecond };
  } finally {
    void ctx.close();
  }
}
