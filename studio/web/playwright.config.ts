import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

const dataDir = process.env.FACADEKIT_E2E_DATA ?? mkdtempSync(join(tmpdir(), "facadekit-e2e-"));

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  workers: 1,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:8137" },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: {
          args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
        },
      },
    },
  ],
  webServer: {
    command: `uv run facadekit serve --no-browser --port 8137 --data-dir "${dataDir}"`,
    cwd: "../backend",
    url: "http://127.0.0.1:8137/api/v1/health",
    reuseExistingServer: true,
    timeout: 60000,
  },
});
