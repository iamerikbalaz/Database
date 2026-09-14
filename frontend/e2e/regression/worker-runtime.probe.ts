import { spawnSync } from "node:child_process";
import { test as base } from "../auth-fixture.ts";
import type { E2eAuthCredentials } from "../auth-credentials.ts";
import { markPhase } from "../safe-diagnostics.ts";

function hostileError(credentials: E2eAuthCredentials): Error {
  return new Error(JSON.stringify({
    password: credentials.initialPassword,
    cookie: `cookie-${credentials.password}`,
    csrf: `csrf-${credentials.password}`,
  }));
}
const test = base.extend<{ ordinaryProbe: void }>({
  ordinaryProbe: async ({ authCredentials }, provideFixture, testInfo) => {
    await provideFixture();
    if (process.env.E2E_LIFECYCLE_PROBE_FAILURE === "teardown") {
      markPhase(testInfo, "change-password");
      throw hostileError(authCredentials);
    }
  },
});
test.afterEach(async ({ authCredentials }, testInfo) => {
  if (process.env.E2E_LIFECYCLE_PROBE_FAILURE === "afterEach") {
    markPhase(testInfo, "logout");
    throw hostileError(authCredentials);
  }
});

for (const name of ["first independent worker", "second independent worker"]) {
  test(name, async ({ authCredentials, launchOptions, ordinaryProbe }, testInfo) => {
    void ordinaryProbe;
    const names = ["E2E_AUTH_EMAIL", "E2E_AUTH_INITIAL_PASSWORD", "E2E_AUTH_PASSWORD"];
    if (!authCredentials.email || !authCredentials.initialPassword || !authCredentials.password) {
      throw new Error("E2E_PROBE_COPY_MISSING");
    }
    if (process.env.E2E_LIFECYCLE_PROBE_FAILURE === "body") {
      markPhase(testInfo, "login");
      // Hostile synthetic exception: the shared fixture must scrub it before
      // either reporting OR Playwright's framework artifact serialization.
      throw hostileError(authCredentials);
    }
    if (names.some((key) => process.env[key] !== undefined || launchOptions.env?.[key] !== undefined)) {
      throw new Error("E2E_PROBE_ENV_NOT_SCRUBBED");
    }
    const browserNames = [...names, "POSTGRES_PASSWORD", "E2E_RUN_TOKEN", "E2E_RUN_MANIFEST"];
    if (browserNames.some((key) => launchOptions.env?.[key] !== undefined)) throw new Error("E2E_PROBE_BROWSER_OPTIONS_UNSAFE");
    await import("../auth-credentials.ts");
    await import("../auth-credentials.ts");
    const browser = spawnSync(process.execPath, ["-e",
      `if (${JSON.stringify(browserNames)}.some(name => process.env[name] !== undefined)) process.exit(21);`,
    ], { env: launchOptions.env, stdio: ["ignore", "pipe", "pipe"] });
    if (browser.status !== 0 || browser.stdout.length || browser.stderr.length) {
      throw new Error("E2E_PROBE_BROWSER_INHERITANCE_FAILED");
    }
    process.stdout.write(`PROBE_WORKER_READY:${testInfo.workerIndex}\n`);
  });
}
