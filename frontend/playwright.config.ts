import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.E2E_FRONTEND_URL;
if (!baseURL) {
  throw new Error("E2E_FRONTEND_URL must be set by scripts/test-demo-e2e.ps1.");
}

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  forbidOnly: true,
  outputDir: process.env.E2E_OUTPUT_DIR ?? "test-results",
  reporter: "line",
  use: {
    baseURL,
    ...devices["Desktop Chrome"],
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
