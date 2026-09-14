import { defineConfig, devices } from "@playwright/test";
import { e2eOutputDirectory, runManifest } from "./e2e/run-manifest";

// Prevent Playwright's automatic failure context from copying the live page,
// which can contain a still-filled password control when a test is interrupted.
process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  forbidOnly: true,
  outputDir: e2eOutputDirectory,
  reporter: "./e2e/safe-reporter.ts",
  use: {
    baseURL: runManifest.frontendUrl,
    ...devices["Desktop Chrome"],
    screenshot: "off",
    trace: "off",
    video: "off",
  },
});
