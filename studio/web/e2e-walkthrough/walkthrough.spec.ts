import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { PNG } from "pngjs";

const OUT = process.env.FACADEKIT_SHOTS ?? join("test-results", "screens");

/** A 320x180 test picture: magenta with a green panel. */
function testPicture(): Buffer {
  const png = new PNG({ width: 320, height: 180 });
  for (let y = 0; y < 180; y++) {
    for (let x = 0; x < 320; x++) {
      const i = (y * 320 + x) * 4;
      const inner = x > 20 && x < 300 && y > 20 && y < 160;
      png.data[i] = inner ? 40 : 230;
      png.data[i + 1] = inner ? 200 : 30;
      png.data[i + 2] = inner ? 80 : 200;
      png.data[i + 3] = 255;
    }
  }
  return PNG.sync.write(png);
}

async function shot(page: Page, project: string, name: string) {
  const dir = join(OUT, project);
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`), fullPage: true });
}

/** Nothing on the page may be wider than the window (the usual way a layout breaks). */
async function expectNoSidewaysScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

/**
 * A light accessibility check without a new dependency: every visible control has a name,
 * no positive tabindex, every image has alt text, ids are unique, the page has a heading,
 * and Tab moves the focus to visible elements. Returns the problems found.
 */
async function a11yProblems(page: Page): Promise<string[]> {
  const problems = await page.evaluate(() => {
    const out: string[] = [];
    const visible = (el: Element) => {
      const r = (el as HTMLElement).getClientRects();
      const st = getComputedStyle(el);
      return r.length > 0 && st.visibility !== "hidden" && st.display !== "none";
    };
    const nameOf = (el: HTMLElement): string => {
      const by = el.getAttribute("aria-labelledby");
      if (by)
        return by
          .split(/\s+/)
          .map((id) => document.getElementById(id)?.textContent ?? "")
          .join(" ")
          .trim();
      const label = el.getAttribute("aria-label");
      if (label) return label.trim();
      if ("labels" in el) {
        const labels = (el as HTMLInputElement).labels;
        if (labels && labels.length > 0)
          return Array.from(labels)
            .map((l) => l.textContent ?? "")
            .join(" ")
            .trim();
      }
      if (el.tagName === "BUTTON" || el.tagName === "A" || el.getAttribute("role") === "button") {
        const t = (el.textContent ?? "").trim();
        if (t) return t;
      }
      return (el.getAttribute("title") ?? "").trim();
    };
    const controls = document.querySelectorAll<HTMLElement>(
      "button, a[href], input:not([type=hidden]), select, textarea, [role=button], canvas[tabindex]",
    );
    for (const el of Array.from(controls)) {
      if (!visible(el)) continue;
      if (!nameOf(el)) out.push(`no name: ${el.outerHTML.slice(0, 120)}`);
    }
    for (const el of Array.from(document.querySelectorAll<HTMLElement>("[tabindex]"))) {
      if (Number(el.getAttribute("tabindex")) > 0) out.push(`positive tabindex: ${el.tagName}`);
    }
    for (const img of Array.from(document.querySelectorAll("img"))) {
      if (!img.hasAttribute("alt")) out.push(`img without alt: ${img.src.slice(-60)}`);
    }
    const ids = new Map<string, number>();
    for (const el of Array.from(document.querySelectorAll("[id]")))
      ids.set(el.id, (ids.get(el.id) ?? 0) + 1);
    for (const [id, n] of ids) if (n > 1) out.push(`duplicate id: ${id}`);
    if (!document.querySelector("h1, h2")) out.push("no heading");
    return out;
  });
  // Focus order: Tab from the top reaches visible, focusable elements.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur?.());
  for (let i = 0; i < 4; i++) {
    await page.keyboard.press("Tab");
    const ok = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return !!el && el !== document.body && el.getClientRects().length > 0;
    });
    if (!ok) problems.push(`Tab ${i + 1} went nowhere visible`);
  }
  return problems;
}

async function check(page: Page) {
  await expectNoSidewaysScroll(page);
  expect(await a11yProblems(page)).toEqual([]);
}

test("from no project to a finished scan", async ({ page }, info) => {
  const who = info.project.name;
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  // First run (the projects share one data folder, so only the first one sees it): the four
  // steps on one screen, and Start goes to the name field. Later: folded away.
  await page.waitForLoadState("networkidle");
  const first = await page.getByText("No projects yet.").isVisible();
  if (first) {
    await expect(page.getByRole("region", { name: /Welcome/ })).toBeVisible();
  } else {
    await expect(page.getByText("How FacadeKit works (4 steps)")).toBeVisible();
  }
  await shot(page, who, "01-projects-empty");
  await check(page);
  if (first) {
    await page.getByRole("button", { name: "Start your first project" }).click();
    await expect(page.getByLabel("Project name")).toBeFocused();
  }
  // Help: ? opens it, Esc closes it.
  await page.locator("body").click({ position: { x: 2, y: 200 } });
  await page.keyboard.press("?");
  await expect(page.getByRole("dialog", { name: "How FacadeKit works" })).toBeVisible();
  await shot(page, who, "01b-help");
  await expectNoSidewaysScroll(page);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await page.getByLabel("Project name").fill(`Demo house ${who}`);
  await page.getByRole("button", { name: "New project" }).click();

  // Step 1: projector (simulated)
  await expect(page.getByText("Step 1 of 3")).toBeVisible();
  await expect(page.getByText(/Simulator mode/)).toBeVisible();
  await shot(page, who, "02-setup-projector");
  await expectNoSidewaysScroll(page);
  await page.getByRole("button", { name: "Next" }).click();

  // Step 2: camera
  await expect(page.getByText("Step 2 of 3")).toBeVisible();
  await expect(page.getByAltText("Live camera view")).toBeVisible();
  await shot(page, who, "03-setup-camera");
  await page.getByRole("button", { name: "Check the camera view" }).click();
  await expect(page.getByText("Your camera sees the projection clearly.")).toBeVisible({
    timeout: 60000,
  });
  await shot(page, who, "04-setup-camera-checked");
  await expectNoSidewaysScroll(page);
  await page.getByRole("button", { name: "Next" }).click();

  // Step 3: scan
  await expect(page.getByText("Step 3 of 3")).toBeVisible();
  await shot(page, who, "05-setup-scan-ready");
  await page.getByRole("button", { name: "Start scan" }).click();
  await expect(page.getByRole("progressbar")).toBeVisible();
  await shot(page, who, "06-setup-scan-running");
  await expect(page.getByText(/Measured \d+% of the projection/)).toBeVisible({ timeout: 90000 });
  await shot(page, who, "07-setup-scan-done");
  await expectNoSidewaysScroll(page);

  await page.getByRole("button", { name: "Continue to surfaces" }).click();
  await expect(page).toHaveURL(/\/surfaces$/);
  await expect(page.getByText("No surfaces yet.")).toBeVisible();
  await expect(page.getByTestId("next-step")).toContainText("Magic select");
  await shot(page, who, "08-surfaces-empty");
  await check(page);
  await page.getByRole("button", { name: "Add rectangle" }).click();
  await expect(page.getByRole("heading", { name: "Surfaces (1)" })).toBeVisible();
  await page.waitForLoadState("networkidle");
  await expect(page.getByTestId("save-status")).toHaveText("All changes saved");
  // The one-time tip on how to drag corners.
  await expect(page.getByRole("note", { name: "Tip" })).toBeVisible();
  await shot(page, who, "09-surfaces");
  await check(page);
  await page.getByRole("button", { name: "Got it" }).click();
  // Undo and redo the new surface.
  await page.getByRole("button", { name: /Undo/ }).click();
  await expect(page.getByRole("heading", { name: "Surfaces (0)" })).toBeVisible();
  await page.getByRole("button", { name: /Redo/ }).click();
  await expect(page.getByRole("heading", { name: "Surfaces (1)" })).toBeVisible();
  // A What-is-this note.
  await page.getByRole("button", { name: "What is Real-world shape?" }).click();
  await expect(page.getByRole("note").filter({ hasText: "squashed" })).toBeVisible();
  await shot(page, who, "09a-surfaces-infotip");
  await expectNoSidewaysScroll(page);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Surface 1", exact: true }).click();
  // Check the surface on the (simulated) house: camera and projector still, light on target.
  await page.getByRole("button", { name: "Check on the house" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: /On target|Check it|Off target/ }),
  ).toBeVisible({ timeout: 60000 });
  await shot(page, who, "09b-surfaces-checked");

  await page.getByRole("link", { name: "Arrange →" }).click();
  await expect(page).toHaveURL(/\/arrange$/);
  await page.waitForLoadState("networkidle");
  await shot(page, who, "10-arrange-empty");
  await check(page);
  await page.getByLabel("Import files").setInputFiles({
    name: "door.png",
    mimeType: "image/png",
    buffer: testPicture(),
  });
  await expect(page.getByText("door.png").first()).toBeVisible({ timeout: 30000 });
  await page.getByRole("button", { name: "Add clip" }).click();
  await page.getByRole("button", { name: "Add to timeline" }).click();
  await expect(page.getByRole("link", { name: "Publish →" })).toBeVisible();
  // The exact frame from the server replaces the quick WebGL preview once nothing changes.
  await expect(page.getByAltText("Exact rendered frame")).toBeVisible({ timeout: 30000 });
  await shot(page, who, "11-arrange");
  await check(page);
  // The timeline is on screen without scrolling at desktop and laptop sizes.
  if (who !== "phone") {
    const box = await page.getByRole("group", { name: "Timeline zoom" }).boundingBox();
    expect(box && box.y + box.height).toBeLessThan(page.viewportSize()!.height);
  }
  // Remove the clip, then bring it back from the toast.
  await page.getByRole("button", { name: "Remove clip" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Clip removed." })).toBeVisible();
  await shot(page, who, "11b-arrange-undo-toast");
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(page.getByRole("button", { name: "Clip door.png" })).toBeVisible();

  await page.getByRole("link", { name: "Publish →" }).click();
  await expect(page).toHaveURL(/\/publish$/);
  await expect(page.getByText(/No renders yet\./)).toBeVisible();
  await shot(page, who, "12-publish-empty");
  await check(page);
  await page.getByRole("button", { name: "Render show" }).click();
  await expect(page.getByRole("list", { name: "Renders" })).toBeVisible({ timeout: 120000 });
  await shot(page, who, "13-publish");
  await check(page);

  await page.goto("/");
  await expect(page.getByText(`Demo house ${who}`)).toBeVisible();
  await shot(page, who, "14-projects");
  await check(page);
  expect(errors).toEqual([]);
});

test("sample project from Projects to a finished render, no hardware", async ({ page }, info) => {
  const who = info.project.name;
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Try a sample project" })).toBeVisible();
  await page.getByLabel("Theme").selectOption("winter");
  await shot(page, who, "20-projects-sample");
  await page.getByRole("button", { name: "Try a sample project" }).click();

  // The simulated scan, surfaces and show are made in a job; the sample opens in Arrange.
  await expect(page).toHaveURL(/\/arrange$/, { timeout: 120000 });
  await page.waitForLoadState("networkidle");
  await expect(page.getByAltText("Exact rendered frame")).toBeVisible({ timeout: 30000 });
  await shot(page, who, "21-sample-arrange");
  await expectNoSidewaysScroll(page);

  await page.getByRole("link", { name: "Publish →" }).click();
  await expect(page).toHaveURL(/\/publish$/);
  await page.getByRole("button", { name: "Render show" }).click();
  await expect(page.getByRole("list", { name: "Renders" })).toBeVisible({ timeout: 180000 });
  await shot(page, who, "22-sample-publish");
  await expectNoSidewaysScroll(page);

  await page.goto("/");
  await expect(page.getByText("Sample: Winter (simulated house)").first()).toBeVisible();
  expect(errors).toEqual([]);
});
