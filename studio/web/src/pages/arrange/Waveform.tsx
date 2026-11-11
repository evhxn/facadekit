import { useEffect, useRef, useState, type RefObject } from "react";
import { loadPeaks, type Peaks } from "./peaks";
import { xToMs } from "./timelineMath";

export const WAVE_H = 64;
const FALLBACK_COLOUR = "#3a7bd5";

interface Props {
  url: string;
  pxPerMs: number;
  /** Full timeline content width in px (the row spans it). */
  contentWidth: number;
  /** Top of the row inside the timeline content, in px. */
  top: number;
  scrollerRef: RefObject<HTMLDivElement>;
}

interface Load {
  url: string;
  peaks: Peaks | null;
  failed: boolean;
}

interface View {
  scrollLeft: number;
  widthPx: number;
}

/**
 * Audio waveform row. Only the visible pixel columns are drawn, so the canvas
 * is sized to the scroller's viewport and moved to follow scrolling.
 */
export default function Waveform({ url, pxPerMs, contentWidth, top, scrollerRef }: Props) {
  const [load, setLoad] = useState<Load | null>(null);
  const [view, setView] = useState<View>({ scrollLeft: 0, widthPx: 0 });
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let cancelled = false;
    loadPeaks(url).then(
      (peaks) => {
        if (!cancelled) setLoad({ url, peaks, failed: false });
      },
      () => {
        if (!cancelled) setLoad({ url, peaks: null, failed: true });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [url]);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const sync = () => setView({ scrollLeft: el.scrollLeft, widthPx: el.clientWidth });
    sync();
    el.addEventListener("scroll", sync, { passive: true });
    // ResizeObserver is absent in some environments (e.g. jsdom); fall back to scroll-only sync.
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(sync) : null;
    ro?.observe(el);
    return () => {
      el.removeEventListener("scroll", sync);
      ro?.disconnect();
    };
  }, [scrollerRef]);

  const current = load && load.url === url ? load : null;
  const peaks = current?.peaks ?? null;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !peaks) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.floor(view.widthPx));
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(WAVE_H * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, WAVE_H);
    const colour =
      getComputedStyle(canvas).getPropertyValue("--waveform-color").trim() || FALLBACK_COLOUR;
    ctx.fillStyle = colour;

    const mid = WAVE_H / 2;
    const n = peaks.max.length;
    const bps = peaks.bucketsPerSecond;
    for (let x = 0; x < w; x++) {
      const contentX = view.scrollLeft + x;
      const b0 = Math.floor((xToMs(contentX, pxPerMs) * bps) / 1000);
      const b1 = Math.min(
        n,
        Math.max(b0 + 1, Math.ceil((xToMs(contentX + 1, pxPerMs) * bps) / 1000)),
      );
      if (b0 >= n || b0 < 0) continue;
      let lo = Infinity;
      let hi = -Infinity;
      for (let b = b0; b < b1; b++) {
        if (peaks.min[b]! < lo) lo = peaks.min[b]!;
        if (peaks.max[b]! > hi) hi = peaks.max[b]!;
      }
      const yTop = mid - hi * mid;
      const yBottom = mid - lo * mid;
      ctx.fillRect(x, yTop, 1, Math.max(1, yBottom - yTop));
    }
  }, [peaks, pxPerMs, view]);

  const left = view.scrollLeft;
  return (
    <div
      data-testid="waveform-row"
      style={{
        position: "absolute",
        left: 0,
        top,
        width: contentWidth,
        height: WAVE_H,
        overflow: "hidden",
        pointerEvents: "none",
        borderTop: "1px solid #8882",
        boxSizing: "border-box",
      }}
    >
      {peaks ? (
        <canvas
          ref={canvasRef}
          aria-label="Audio waveform"
          style={{
            position: "absolute",
            left,
            top: 0,
            width: Math.max(1, view.widthPx),
            height: WAVE_H,
          }}
        />
      ) : (
        <span
          style={{
            position: "absolute",
            left: left + 8,
            top: 0,
            lineHeight: `${WAVE_H}px`,
            fontSize: 11,
            opacity: 0.6,
          }}
        >
          {current?.failed ? "Waveform unavailable. Import the audio again." : "Loading waveform…"}
        </span>
      )}
    </div>
  );
}
