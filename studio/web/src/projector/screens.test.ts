import { describe, expect, it } from "vitest";
import { canListScreens, describeScreen, likelyProjector, listScreens } from "./screens";

const laptop = { label: "Built-in Retina Display", width: 1512, height: 982, devicePixelRatio: 2, isInternal: true };
const beamer = { label: "EPSON PJ", width: 1920, height: 1080, devicePixelRatio: 1, isInternal: false };
const tv = { label: "LG TV", width: 1920, height: 1080, devicePixelRatio: 1, isInternal: false };

const win = (screens: object[], current: object) =>
  ({ getScreenDetails: async () => ({ screens, currentScreen: current }) }) as unknown as Window;

describe("projector screens", () => {
  it("names every screen in physical pixels and marks the one the window is on", async () => {
    const screens = await listScreens(win([laptop, beamer], laptop));
    expect(screens?.map(describeScreen)).toEqual([
      "Built-in Retina Display (built-in screen, 3024 × 1964, scaled 2×)",
      "EPSON PJ (external, 1920 × 1080)",
    ]);
    expect(screens?.map((s) => s.current)).toEqual([true, false]);
  });

  it("picks the projector only when exactly one screen is external", async () => {
    expect(likelyProjector((await listScreens(win([laptop, beamer], laptop)))!)?.label).toBe("EPSON PJ");
    expect(likelyProjector((await listScreens(win([laptop, beamer, tv], laptop)))!)).toBeNull();
    expect(likelyProjector((await listScreens(win([laptop], laptop)))!)).toBeNull();
  });

  it("gives null where the browser cannot list screens or the user refuses", async () => {
    expect(canListScreens({} as Window)).toBe(false);
    expect(await listScreens({} as Window)).toBeNull();
    const refused = {
      getScreenDetails: async () => {
        throw new DOMException("denied", "NotAllowedError");
      },
    } as unknown as Window;
    expect(canListScreens(refused)).toBe(true);
    expect(await listScreens(refused)).toBeNull();
  });
});
