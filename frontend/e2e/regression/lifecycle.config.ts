import { defineConfig } from "@playwright/test";

// Infrastructure-only probe: no app, server, credentials file or HTTP requests.
// The production config still enforces its repository-local capability manifest.
process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";
export default defineConfig({
  testDir: ".",
  testMatch: "worker-runtime.probe.ts",
  outputDir: process.env.E2E_LIFECYCLE_PROBE_OUTPUT,
  preserveOutput: "always",
  fullyParallel: true,
  workers: 2,
  reporter: "line",
  use: { screenshot: "off", trace: "off", video: "off" },
});
