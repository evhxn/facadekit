import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vitest/config";

const outDir = "../backend/src/facadekit/webdist";

// emptyOutDir removes webdist/.gitkeep; put it back after every build.
function keepGitkeep(): Plugin {
  return {
    name: "keep-gitkeep",
    apply: "build",
    closeBundle() {
      mkdirSync(resolve(outDir), { recursive: true });
      writeFileSync(resolve(outDir, ".gitkeep"), "");
    },
  };
}

export default defineConfig({
  plugins: [react(), keepGitkeep()],
  build: { outDir, emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8137",
      "/ws": { target: "ws://127.0.0.1:8137", ws: true },
    },
  },
  test: { environment: "jsdom" },
});
