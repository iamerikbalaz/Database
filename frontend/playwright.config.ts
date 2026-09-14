import { defineConfig, devices } from "@playwright/test";
import { e2eOutputDirectory, runManifest } from "./e2e/run-manifest";

// Prevent Playwright's automatic failure context from copying the live page,
// which can contain a still-filled password control when a test is interrupted.
process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";

const browserSecretEnvironmentNames = new Set([
  "POSTGRES_PASSWORD",
  "E2E_RUN_MANIFEST",
  "E2E_RUN_TOKEN",
  "E2E_AUTH_EMAIL",
  "E2E_AUTH_INITIAL_PASSWORD",
  "E2E_AUTH_PASSWORD",
]);

const browserEnvironment: Record<string, string> = {};
for (const [name, value] of Object.entries(process.env)) {
  if (value !== undefined && !browserSecretEnvironmentNames.has(name)) {
    browserEnvironment[name] = value;
  }
}

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
    launchOptions: { env: browserEnvironment },
    screenshot: "off",
    trace: "off",
    video: "off",
  },
});
