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

test("from no project to a finished scan", async ({ page }, info) => {
  const who = info.project.name;
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await shot(page, who, "01-projects-empty");
  await expectNoSidewaysScroll(page);

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
  await shot(page, who, "08-surfaces-empty");
  await page.getByRole("button", { name: "Add rectangle" }).click();
  await expect(page.getByRole("heading", { name: "Surfaces (1)" })).toBeVisible();
  await page.waitForLoadState("networkidle");
  await shot(page, who, "09-surfaces");

  await page.getByRole("link", { name: "Arrange →" }).click();
  await expect(page).toHaveURL(/\/arrange$/);
  await page.waitForLoadState("networkidle");
  await shot(page, who, "10-arrange-empty");
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
  await expectNoSidewaysScroll(page);

  await page.getByRole("link", { name: "Publish →" }).click();
  await expect(page).toHaveURL(/\/publish$/);
  await expect(page.getByText("No renders yet.")).toBeVisible();
  await shot(page, who, "12-publish-empty");
  await page.getByRole("button", { name: "Render show" }).click();
  await expect(page.getByRole("list", { name: "Renders" })).toBeVisible({ timeout: 120000 });
  await shot(page, who, "13-publish");
  await expectNoSidewaysScroll(page);

  await page.goto("/");
  await expect(page.getByText(`Demo house ${who}`)).toBeVisible();
  await shot(page, who, "14-projects");
  expect(errors).toEqual([]);
});
