import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

// Dedicated port: distinct from the dev default (8137) so e2e never meets a dev server.
const E2E_PORT = 8197;
const dataDir = process.env.FACADEKIT_E2E_DATA ?? mkdtempSync(join(tmpdir(), "facadekit-e2e-"));

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  workers: 1,
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${E2E_PORT}` },
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
    command: `uv run facadekit serve --no-browser --port ${E2E_PORT} --data-dir "${dataDir}"`,
    cwd: "../backend",
    url: `http://127.0.0.1:${E2E_PORT}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 60000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 5000 },
  },
});
