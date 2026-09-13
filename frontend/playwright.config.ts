import { defineConfig, devices } from "@playwright/test";
import { e2eOutputDirectory, runManifest } from "./e2e/run-manifest";

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
  reporter: "line",
  use: {
    baseURL: runManifest.frontendUrl,
    ...devices["Desktop Chrome"],
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
