import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { lstatSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const frontendRoot = fileURLToPath(new URL("..", import.meta.url));
const moduleUrl = new URL("./auth-credentials.ts", import.meta.url).href;
const credentialNames = ["E2E_AUTH_EMAIL", "E2E_AUTH_INITIAL_PASSWORD", "E2E_AUTH_PASSWORD"];
const credentials = () => ({
  E2E_AUTH_EMAIL: `e2e.admin.${randomBytes(16).toString("hex")}@example.invalid`,
  E2E_AUTH_INITIAL_PASSWORD: `E2E!${randomBytes(32).toString("hex")}`,
  E2E_AUTH_PASSWORD: `E2E!${randomBytes(32).toString("hex")}`,
});

async function child(args: string[], env: NodeJS.ProcessEnv) {
  const process_ = spawn(process.execPath, args, { cwd: frontendRoot, env, stdio: ["ignore", "pipe", "pipe"] });
  let stdout = "", stderr = "";
  process_.stdout.on("data", (data) => { stdout += String(data); });
  process_.stderr.on("data", (data) => { stderr += String(data); });
  const status = await new Promise<number | null>((resolve, reject) => {
    process_.on("error", () => reject(new Error("E2E_PROBE_SPAWN_FAILED")));
    process_.on("close", resolve);
  });
  return { status, stdout, stderr };
}
function assertNoSecrets(text: string, values: string[]) {
  // Never pass actual/expected secret strings to an assertion formatter.
  assert.equal(values.some((value) => text.includes(value)), false, "E2E_PROBE_SECRET_LEAK");
}
function inspectArtifacts(directory: string, values: string[]): number {
  let errorContexts = 0;
  for (const name of readdirSync(directory)) {
    const target = path.join(directory, name);
    const stat = lstatSync(target);
    assert.equal(stat.isSymbolicLink(), false, "E2E_PROBE_UNEXPECTED_LINK");
    if (stat.isDirectory()) errorContexts += inspectArtifacts(target, values);
    else {
      const content = readFileSync(target).toString("utf8");
      assertNoSecrets(content, values);
      if (name === "error-context.md") {
        assert.equal(content.includes("REAWOTE_SAFE_E2E:"), true, "E2E_PROBE_UNSANITIZED_CONTEXT");
        errorContexts++;
      }
    }
  }
  return errorContexts;
}

test("discovery imports retain credentials for independent runtime workers and their browser children", async () => {
  const values = credentials();
  const code = `
    const url = ${JSON.stringify(moduleUrl)};
    const environment = process.env;
    process.env = new Proxy(environment, {
      get(target, name) {
        if (String(name).startsWith('E2E_AUTH_')) throw new Error('E2E_PROBE_IMPORT_READ_ENV');
        return target[name];
      },
      deleteProperty(target, name) {
        if (String(name).startsWith('E2E_AUTH_')) throw new Error('E2E_PROBE_IMPORT_DELETED_ENV');
        return delete target[name];
      }
    });
    await import(url);
    await import(url + '?discovery-again');
    process.env = environment;
    const names = ${JSON.stringify(credentialNames)};
    if (names.some(name => !process.env[name])) throw new Error('E2E_PROBE_CONTROLLER_ENV_LOST');
    const { spawnSync } = await import('node:child_process');
    const workerCode = \`
      const helper = await import(\${JSON.stringify(url)});
      const value = helper.loadWorkerAuthCredentials();
      const names = \${JSON.stringify(names)};
      if (names.some(name => process.env[name] !== undefined)) throw new Error('E2E_PROBE_WORKER_ENV_RETAINED');
      await import(\${JSON.stringify(url)} + '?worker-again');
      if (!value.initialPassword || !value.password) throw new Error('E2E_PROBE_COPY_MISSING');
      const { spawnSync } = await import('node:child_process');
      const probe = spawnSync(process.execPath, ['-e',
        'if (' + JSON.stringify(names) + '.some(name => process.env[name] !== undefined)) process.exit(23);'
      ], { stdio: ['ignore', 'pipe', 'pipe'] });
      if (probe.status !== 0 || probe.stdout.length || probe.stderr.length) throw new Error('E2E_PROBE_BROWSER_ENV_LEAK');
    \`;
    for (let index = 0; index < 2; index++) {
      const worker = spawnSync(process.execPath, ['--experimental-strip-types', '--input-type=module', '-e', workerCode],
        { env: process.env, stdio: ['ignore', 'pipe', 'pipe'] });
      if (worker.status !== 0 || worker.stdout.length || worker.stderr.length) throw new Error('E2E_PROBE_WORKER_FAILED');
    }
    if (names.some(name => !process.env[name])) throw new Error('E2E_PROBE_CONTROLLER_CHANGED');
  `;
  const result = await child(["--experimental-strip-types", "--input-type=module", "-e", code], { ...process.env, ...values });
  assertNoSecrets(result.stdout + result.stderr, Object.values(values));
  assert.equal(result.status, 0, "E2E_PROBE_LIFECYCLE_FAILED");
});

test("missing credentials fail with a safe code and no environment key or value", async () => {
  const env = { ...process.env };
  for (const name of credentialNames) delete env[name];
  const code = `
    const helper = await import(${JSON.stringify(moduleUrl)});
    try { helper.loadWorkerAuthCredentials(); process.exitCode = 9; }
    catch (error) { process.stdout.write(error.message); }
  `;
  const result = await child(["--experimental-strip-types", "--input-type=module", "-e", code], env);
  assert.equal(result.status, 0, "E2E_PROBE_MISSING_CASE_FAILED");
  assert.equal(result.stdout.includes("E2E_CREDENTIALS_MISSING"), true);
  assertNoSecrets(result.stdout + result.stderr, credentialNames);
});

test("invalid or partial worker credentials are scrubbed without exposing names or values", async () => {
  const values = credentials();
  const code = `
    const helper = await import(${JSON.stringify(moduleUrl)});
    try { helper.loadWorkerAuthCredentials(); process.exitCode = 9; }
    catch (error) {
      process.stdout.write(error.message);
      if (${JSON.stringify(credentialNames)}.some(name => process.env[name] !== undefined)) process.exitCode = 11;
    }
  `;
  for (const override of [{ E2E_AUTH_EMAIL: "invalid-format" }, { E2E_AUTH_INITIAL_PASSWORD: "" }]) {
    const result = await child(["--experimental-strip-types", "--input-type=module", "-e", code], { ...process.env, ...values, ...override });
    assertNoSecrets(result.stdout + result.stderr, [...Object.values(values), ...credentialNames]);
    assert.equal(result.status, 0, "E2E_PROBE_INVALID_CASE_FAILED");
    assert.equal(/E2E_CREDENTIALS_(MISSING|INVALID)/.test(result.stdout), true);
  }
});

test("actual Playwright discovery does not initialize fixtures; two workers scrub environment before browser launch options", async () => {
  const values = credentials();
  const directory = mkdtempSync(path.join(os.tmpdir(), "reawote-e2e-lifecycle-"));
  const safeDirectory = realpathSync(directory);
  const env: NodeJS.ProcessEnv = { ...process.env, E2E_LIFECYCLE_PROBE_OUTPUT: directory };
  for (const name of credentialNames) delete env[name];
  const args = ["node_modules/playwright/cli.js", "test", "--config=e2e/regression/lifecycle.config.ts"];
  try {
    const discovery = await child([...args, "--list"], env);
    assertNoSecrets(discovery.stdout + discovery.stderr, Object.values(values));
    assert.equal(discovery.status, 0, "E2E_PROBE_DISCOVERY_FAILED");
    assert.equal(discovery.stdout.includes("PROBE_WORKER_READY"), false, "E2E_PROBE_DISCOVERY_RAN_FIXTURE");
    const missing = await child([...args, "--reporter=./e2e/safe-reporter.ts"], env);
    assert.equal(missing.status, 1, "E2E_PROBE_MISSING_RUNTIME_MUST_FAIL");
    assert.equal(missing.stdout.includes("code=E2E_CREDENTIALS_MISSING; phase=worker initialization"), true);
    assertNoSecrets(missing.stdout + missing.stderr, [...Object.values(values), ...credentialNames]);
    inspectArtifacts(directory, Object.values(values));
    const runtime = await child(args, { ...env, ...values, POSTGRES_PASSWORD: values.E2E_AUTH_INITIAL_PASSWORD, E2E_RUN_TOKEN: values.E2E_AUTH_PASSWORD });
    assertNoSecrets(runtime.stdout + runtime.stderr, Object.values(values));
    inspectArtifacts(directory, Object.values(values));
    assert.equal(runtime.status, 0, "E2E_PROBE_PLAYWRIGHT_RUNTIME_FAILED");
    const workers = new Set([...runtime.stdout.matchAll(/PROBE_WORKER_READY:(\d+)/g)].map((match) => match[1]));
    assert.equal(workers.size, 2, "E2E_PROBE_TWO_WORKERS_REQUIRED");
    for (const [failureMode, phase] of [["body", "login"], ["afterEach", "logout"], ["teardown", "change-password"]]) {
      const failure = await child([...args, "--reporter=./e2e/safe-reporter.ts"], {
        ...env, ...values, E2E_LIFECYCLE_PROBE_FAILURE: failureMode,
      });
      assertNoSecrets(failure.stdout + failure.stderr, Object.values(values));
      assert.equal(inspectArtifacts(directory, Object.values(values)), 2, "E2E_PROBE_FAILURE_CONTEXTS_REQUIRED");
      assert.equal(failure.status, 1, "E2E_PROBE_FAILURE_MUST_REMAIN_FAILED");
      assert.equal(failure.stdout.includes(`code=E2E_ASSERTION_FAILED; phase=${phase}`), true, "E2E_PROBE_SAFE_FAILURE_PHASE_MISSING");
    }
  } finally {
    // Delete only the exact directory created for this regression, never a root.
    assert.equal(realpathSync(directory), safeDirectory);
    assert.equal(path.dirname(directory), os.tmpdir());
    assert.equal(path.basename(directory).startsWith("reawote-e2e-lifecycle-"), true);
    inspectArtifacts(directory, Object.values(values));
    rmSync(directory, { recursive: true });
  }
});
