import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

// The wizard from an empty data folder to a finished scan, in simulator mode, with a
// screenshot of every screen (docs/10-ui.md). Separate from the parity run: other port,
// other folder, and `--sim`.
const PORT = 8198;
// Software WebGL, as in the parity run: headless Chrome has no GPU, and the Arrange preview
// draws with WebGL.
const gl = {
  launchOptions: {
    args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
  },
};
const dataDir = mkdtempSync(join(tmpdir(), "facadekit-walk-"));

export default defineConfig({
  testDir: "./e2e-walkthrough",
  outputDir: "./test-results/walkthrough",
  workers: 1,
  reporter: "list",
  timeout: 240000,
  use: { baseURL: `http://127.0.0.1:${PORT}`, viewport: { width: 1280, height: 800 } },
  projects: [
    { name: "dark", use: { ...devices["Desktop Chrome"], ...gl, colorScheme: "dark" } },
    { name: "light", use: { ...devices["Desktop Chrome"], ...gl, colorScheme: "light" } },
    { name: "phone", use: { ...devices["Pixel 7"], ...gl, colorScheme: "dark" } },
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
