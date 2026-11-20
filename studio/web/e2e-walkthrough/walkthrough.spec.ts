import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const OUT = process.env.FACADEKIT_SHOTS ?? join("test-results", "screens");

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
  await page.waitForLoadState("networkidle");
  await shot(page, who, "08-surfaces");

  for (const [step, name] of [
    ["Arrange", "09-arrange"],
    ["Publish", "10-publish"],
  ] as const) {
    await page.getByRole("link", { name: step }).click();
    await expect(page).toHaveURL(new RegExp(`/${step.toLowerCase()}$`));
    await page.waitForLoadState("networkidle");
    await shot(page, who, name);
  }

  await page.goto("/");
  await expect(page.getByText(`Demo house ${who}`)).toBeVisible();
  await shot(page, who, "11-projects");
  expect(errors).toEqual([]);
});
