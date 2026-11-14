/**
 * Which screen is the projector? (First hardware test: "I had to guess which monitor.")
 *
 * Chromium's Window Management API can name the screens and put a window full screen on a
 * chosen one. It asks the user for permission once. Other browsers do not have it; then the
 * page keeps its "drag this window to the projector" instructions.
 */

export interface ScreenInfo {
  label: string;
  width: number;
  height: number;
  /** Physical pixels = CSS pixels times this. */
  scale: number;
  internal: boolean;
  current: boolean;
  /** Opaque handle for `requestFullscreen({ screen })`. */
  handle: unknown;
}

interface DetailedScreen {
  label?: string;
  width: number;
  height: number;
  devicePixelRatio?: number;
  isInternal?: boolean;
}
interface ScreenDetails {
  screens: DetailedScreen[];
  currentScreen: DetailedScreen;
}
type WindowWithScreens = Window & { getScreenDetails?: () => Promise<ScreenDetails> };

export function canListScreens(win: Window = window): boolean {
  return typeof (win as WindowWithScreens).getScreenDetails === "function";
}

/** All screens, or null when the browser cannot tell or the user said no. */
export async function listScreens(win: Window = window): Promise<ScreenInfo[] | null> {
  const get = (win as WindowWithScreens).getScreenDetails;
  if (typeof get !== "function") return null;
  try {
    const details = await get.call(win);
    return details.screens.map((s, i) => ({
      label: s.label || `Screen ${i + 1}`,
      width: Math.round(s.width * (s.devicePixelRatio ?? 1)),
      height: Math.round(s.height * (s.devicePixelRatio ?? 1)),
      scale: s.devicePixelRatio ?? 1,
      internal: s.isInternal === true,
      current: s === details.currentScreen,
      handle: s,
    }));
  } catch {
    return null;
  }
}

/**
 * The screen that is most likely the projector: the only external one. With several
 * external screens (or none) there is no safe guess, so the user chooses.
 */
export function likelyProjector(screens: ScreenInfo[]): ScreenInfo | null {
  const external = screens.filter((s) => !s.internal);
  return external.length === 1 && external[0] ? external[0] : null;
}

export function describeScreen(s: ScreenInfo): string {
  const kind = s.internal ? "built-in screen" : "external";
  const scale = Math.abs(s.scale - 1) > 0.01 ? `, scaled ${s.scale}×` : "";
  return `${s.label} (${kind}, ${s.width} × ${s.height}${scale})`;
}
