import { test as base } from "@playwright/test";
import { browserEnvironment, loadWorkerAuthCredentials, type E2eAuthCredentials } from "./auth-credentials.ts";
import { sanitizeFailureDiagnostics } from "./safe-diagnostics.ts";

export const test = base.extend<{ sanitizeDiagnostics: void }, { authCredentials: E2eAuthCredentials }>({
  // No page/context dependencies: teardown runs after these fixtures, but before
  // Playwright's built-in artifact recorder persists the public errors array.
  // eslint-disable-next-line no-empty-pattern
  sanitizeDiagnostics: [async ({}, provideFixture, testInfo) => {
    try { await provideFixture(); }
    finally { sanitizeFailureDiagnostics(testInfo); }
  }, { auto: true, box: true, timeout: 0 }],
  // Playwright requires a destructured first argument, even with no dependencies.
  // eslint-disable-next-line no-empty-pattern
  authCredentials: [async ({}, provideFixture) => {
    const credentials = loadWorkerAuthCredentials();
    await provideFixture(credentials);
  }, { scope: "worker", auto: true }],
  // Browser startup depends on launchOptions, which depends on the credentials
  // fixture. This ordering is explicit, not dependent on incidental hook order.
  launchOptions: async ({ authCredentials, launchOptions }, provideFixture) => {
    void authCredentials;
    await provideFixture({
      ...launchOptions,
      env: browserEnvironment({ ...process.env, ...launchOptions.env }),
    });
  },
});
