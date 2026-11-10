export const MIN_PX_PER_SEC = 10;
export const MAX_PX_PER_SEC = 400;

export function msToX(ms: number, pxPerMs: number): number {
  return ms * pxPerMs;
}

export function xToMs(x: number, pxPerMs: number): number {
  return x / pxPerMs;
}

/** Nearest candidate within tolerancePx (converted to ms), otherwise the input unchanged. */
export function snap(
  ms: number,
  candidates: readonly number[],
  tolerancePx: number,
  pxPerMs: number,
): number {
  const tolMs = tolerancePx / pxPerMs;
  let best = ms;
  let bestDist = Infinity;
  for (const c of candidates) {
    const d = Math.abs(c - ms);
    if (d <= tolMs && d < bestDist) {
      best = c;
      bestDist = d;
    }
  }
  return best;
}

export interface RulerTick {
  ms: number;
  label: string;
}

const STEPS_S = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600];
const MIN_TICK_PX = 70;

function tickLabel(ms: number): string {
  const s = ms / 1000;
  if (s >= 60 && Number.isInteger(s)) {
    const m = Math.floor(s / 60);
    const rest = s - m * 60;
    return `${m}:${String(rest).padStart(2, "0")}`;
  }
  return `${+s.toFixed(1)}s`;
}

/** Ticks covering [viewStartMs, viewEndMs], spaced at least ~70 px apart. */
export function rulerTicks(viewStartMs: number, viewEndMs: number, pxPerMs: number): RulerTick[] {
  const stepS = STEPS_S.find((s) => s * 1000 * pxPerMs >= MIN_TICK_PX) ?? 3600;
  const stepMs = Math.round(stepS * 1000);
  const first = Math.max(0, Math.floor(viewStartMs / stepMs)) * stepMs;
  const ticks: RulerTick[] = [];
  for (let ms = first; ms <= viewEndMs; ms += stepMs) ticks.push({ ms, label: tickLabel(ms) });
  return ticks;
}
