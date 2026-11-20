import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

// The wizard from an empty data folder to a finished scan, in simulator mode, with a
// screenshot of every screen (docs/10-ui.md). Separate from the parity run: other port,
// other folder, and `--sim`.
const PORT = 8198;
const dataDir = mkdtempSync(join(tmpdir(), "facadekit-walk-"));

export default defineConfig({
  testDir: "./e2e-walkthrough",
  outputDir: "./test-results/walkthrough",
  workers: 1,
  reporter: "list",
  timeout: 120000,
  use: { baseURL: `http://127.0.0.1:${PORT}`, viewport: { width: 1280, height: 800 } },
  projects: [
    { name: "dark", use: { ...devices["Desktop Chrome"], colorScheme: "dark" } },
    { name: "phone", use: { ...devices["Pixel 7"], colorScheme: "dark" } },
  ],
  webServer: {
    command: `uv run facadekit serve --sim --no-browser --port ${PORT} --data-dir "${dataDir}"`,
    cwd: "../backend",
    env: { FACADEKIT_NO_CAMERA_PROMPT: "1" },
    url: `http://127.0.0.1:${PORT}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 60000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 5000 },
  },
});
