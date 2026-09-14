import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import type { FullConfig, FullResult, Suite, TestCase, TestResult, TestStep } from "@playwright/test/reporter";
import {
  endpointFromPath, markPhase, readSafeErrorMarker, recordConsole, recordHttp,
  SafeE2eError, safeAnnotationTypes, safeScenarioNames,
} from "./safe-diagnostics.ts";
import SafeReporter from "./safe-reporter.ts";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const testFile = path.join(repositoryRoot, "frontend", "e2e", "material-done.spec.ts");
const secrets = ["synthetic-password", "synthetic-cookie", "synthetic-csrf"].map((kind) => `${kind}-${randomUUID()}`);

function fakeTest(overrides: Partial<TestCase> = {}): TestCase {
  return {
    title: safeScenarioNames[0], location: { file: testFile, line: 123, column: 1 },
    ...overrides,
  } as TestCase;
}

function fakeResult(overrides: Partial<TestResult> = {}): TestResult {
  return {
    status: "failed", annotations: [], errors: [{ message: secrets.join(" ") }],
    ...overrides,
  } as TestResult;
}

function fakeSuite(tests: TestCase[]): Suite {
  return { allTests: () => tests } as Suite;
}

function capture(run: (reporter: SafeReporter) => void): string {
  const stdout = process.stdout.write;
  const stderr = process.stderr.write;
  const chunks: string[] = [];
  process.stdout.write = (chunk: string | Uint8Array) => { chunks.push(chunk.toString()); return true; };
  process.stderr.write = (chunk: string | Uint8Array) => { chunks.push(chunk.toString()); return true; };
  try { run(new SafeReporter()); }
  finally { process.stdout.write = stdout; process.stderr.write = stderr; }
  const output = chunks.join("");
  for (const secret of secrets) assert.equal(output.includes(secret), false, "Complete reporter output must exclude secrets");
  return output;
}

test("safe reporter retains scenario, phase, first failed-step location, endpoint and console category", () => {
  const result = fakeResult();
  markPhase(result, "login");
  recordHttp(result, "/api/auth/login", 503);
  recordConsole(result, "error");
  const output = capture((reporter) => {
    reporter.onStepEnd(fakeTest(), result, {
      location: { file: testFile, line: 178, column: 3 }, annotations: [],
      error: { message: secrets[0] }, title: secrets[1],
    } as unknown as TestStep);
    markPhase(result, "assertion"); // teardown cannot erase the original failure phase
    reporter.onTestEnd(fakeTest(), result);
  });
  assert.match(output, /required password change revokes the initial session and requires a new login/);
  assert.match(output, /code=E2E_ASSERTION_FAILED; phase=login; at=frontend\/e2e\/material-done\.spec\.ts:178/);
  assert.match(output, /HTTP: \/api\/auth\/login 503/);
  assert.match(output, /CONSOLE: error/);
});

test("safe reporter ignores untrusted titles, errors, nested causes, call logs, bodies and attachments", () => {
  const poisoned = secrets.join(" ");
  const target = fakeTest({ title: poisoned, location: { file: poisoned, line: 22, column: 1 } });
  const output = capture((reporter) => {
    reporter.onBegin({} as FullConfig, fakeSuite([target]));
    reporter.onTestEnd(target, fakeResult({
      errors: [{ message: poisoned, stack: poisoned, value: poisoned, snippet: poisoned, cause: { message: poisoned } }],
      stdout: [poisoned], stderr: [poisoned],
      attachments: [{ name: poisoned, contentType: "text/plain", body: Buffer.from(poisoned) }],
      annotations: [
        { type: safeAnnotationTypes.phase, description: `login ${poisoned}` },
        { type: safeAnnotationTypes.http, description: `/api/auth/login 200 ${poisoned}` },
        { type: safeAnnotationTypes.console, description: `error ${poisoned}` },
        { type: poisoned, description: poisoned },
      ],
    }));
    reporter.onEnd({ status: poisoned } as unknown as FullResult);
  });
  assert.match(output, /SCENARIO: unrecognized scenario/);
  assert.match(output, /FAIL: unrecognized scenario; code=E2E_ASSERTION_FAILED; phase=assertion/);
  assert.doesNotMatch(output, /at=|HTTP:|CONSOLE:/);
});

test("only exact allowlisted scenario names are shown for passing and skipped scenarios", () => {
  const output = capture((reporter) => {
    for (const title of safeScenarioNames) reporter.onTestEnd(fakeTest({ title }), fakeResult({ status: "passed" }));
    reporter.onTestEnd(fakeTest({ title: `safe ${secrets[0]}` }), fakeResult({ status: "skipped" }));
  });
  assert.equal(output.split("PASS:").length - 1, 6);
  assert.match(output, /SKIP: unrecognized scenario/);
});

test("worker initialization error survives the real Playwright name-prefix serialization shape", () => {
  const error = new SafeE2eError("E2E_CREDENTIALS_MISSING", "worker initialization");
  const serialized = `${error.name}: ${error.message}`;
  const output = capture((reporter) => {
    reporter.onTestEnd(fakeTest(), fakeResult({ errors: [{ message: serialized, stack: secrets[0] }] }));
  });
  assert.match(output, /code=E2E_CREDENTIALS_MISSING; phase=worker initialization/);
  assert.match(output, /frontend\/e2e\/material-done\.spec\.ts:123/);
});

test("safe error markers are full matches and never promote arbitrary exception text", () => {
  const marker = new SafeE2eError("E2E_CREDENTIALS_INVALID", "worker initialization").message;
  for (const candidate of [
    `${secrets[0]} ${marker}`, `${marker} ${secrets[1]}`, `${marker}\n${secrets[2]}`,
    `Error: ${marker}`, `REAWOTE_SAFE_E2E:${secrets[0]}:login`, `REAWOTE_SAFE_E2E:E2E_HTTP_FAILURE:${secrets[2]}`,
  ]) {
    assert.equal(readSafeErrorMarker(candidate), undefined);
    capture((reporter) => reporter.onError({ message: candidate, stack: secrets[0] }));
  }
  assert.deepEqual(readSafeErrorMarker(marker), { code: "E2E_CREDENTIALS_INVALID", phase: "worker initialization" });
});

test("discovery failure is actionable without disclosing raw subprocess or exception text", () => {
  const output = capture((reporter) => {
    reporter.onError({ message: secrets.join(" "), location: { file: testFile, line: 8, column: 1 } });
  });
  assert.match(output, /code=E2E_DISCOVERY_FAILED; phase=discovery; at=frontend\/e2e\/material-done\.spec\.ts:8/);
});

test("unmarked infrastructure failure after discovery is classified as worker initialization", () => {
  const output = capture((reporter) => {
    reporter.onBegin({} as FullConfig, fakeSuite([]));
    reporter.onError({ message: secrets[0] });
  });
  assert.match(output, /code=E2E_WORKER_INITIALIZATION_FAILED; phase=worker initialization/);
});

test("location accepts exact repo-relative or absolute file and integer line only", () => {
  for (const file of [testFile, "frontend/e2e/material-done.spec.ts", testFile.replaceAll("\\", "/")]) {
    assert.match(capture((reporter) => {
      reporter.onTestEnd(fakeTest({ location: { file, line: 99, column: 1 } }), fakeResult());
    }), /at=frontend\/e2e\/material-done\.spec\.ts:99/);
  }
  const badLocations = [
    { file: `${repositoryRoot}-other/frontend/e2e/material-done.spec.ts`, line: 1 },
    { file: `frontend/e2e/${secrets[0]}/../material-done.spec.ts`, line: 1 },
    { file: `frontend/e2e/material-done.spec.ts?${secrets[1]}`, line: 1 },
    { file: `//${secrets[2]}/frontend/e2e/material-done.spec.ts`, line: 1 },
    { file: testFile, line: secrets[0] }, { file: testFile, line: -1 },
    { file: testFile, line: Number.POSITIVE_INFINITY }, { file: testFile, line: 2.2 },
  ];
  for (const location of badLocations) {
    const output = capture((reporter) => reporter.onTestEnd(
      fakeTest({ location: { ...location, column: 1 } as TestCase["location"] }), fakeResult(),
    ));
    assert.doesNotMatch(output, /at=/);
  }
});

test("HTTP normalization removes IDs and rejects URLs, query data and unsupported endpoints", () => {
  assert.equal(endpointFromPath("/api/auth/session"), "/api/auth/session");
  assert.equal(endpointFromPath(`/api/materials/${randomUUID()}/metadata/snapshots`), "/api/materials/{id}/metadata/snapshots");
  for (const input of [
    `https://example.invalid/api/auth/login?${secrets[0]}`, `/api/auth/session?${secrets[1]}`,
    `/api/materials/${secrets[2]}/metadata`, "/api/auth/new-endpoint", "/api/materials/../auth/login",
  ]) assert.equal(endpointFromPath(input), undefined);
});

test("untrusted annotation status and category values cannot inject additional report fields", () => {
  const result = fakeResult({ annotations: [
    { type: safeAnnotationTypes.http, description: `/api/auth/login ${secrets[0]}` },
    { type: safeAnnotationTypes.http, description: "/api/auth/login 200\n" },
    { type: safeAnnotationTypes.http, description: "/api/auth/login 099" },
    { type: safeAnnotationTypes.http, description: "/api/auth/login 600" },
    { type: safeAnnotationTypes.console, description: `warning\n${secrets[1]}` },
    { type: safeAnnotationTypes.phase, description: `logout\n${secrets[2]}` },
  ] });
  const output = capture((reporter) => reporter.onTestEnd(fakeTest(), result));
  assert.doesNotMatch(output, /HTTP:|CONSOLE:/);
  assert.match(output, /phase=assertion/);
});

test("timeout and interruption retain safe internal codes", () => {
  const output = capture((reporter) => {
    reporter.onTestEnd(fakeTest(), fakeResult({ status: "timedOut" }));
    reporter.onTestEnd(fakeTest(), fakeResult({ status: "interrupted" }));
  });
  assert.match(output, /code=E2E_TEST_TIMEOUT/);
  assert.match(output, /code=E2E_RUN_INTERRUPTED/);
});

test("a real run requires all six scenarios passed and never accepts one passed with five skipped", async () => {
  const outcomes: Array<ReturnType<SafeReporter["onEnd"]>> = [];
  capture((reporter) => {
    reporter.onBegin({} as FullConfig, fakeSuite(safeScenarioNames.map((title) => fakeTest({ title }))));
    for (const [index, title] of safeScenarioNames.entries()) {
      reporter.onTestEnd(fakeTest({ title }), fakeResult({ status: index === 0 ? "passed" : "skipped" }));
    }
    outcomes.push(reporter.onEnd({ status: "passed" } as FullResult));
  });
  capture((reporter) => {
    reporter.onBegin({} as FullConfig, fakeSuite(safeScenarioNames.map((title) => fakeTest({ title }))));
    for (const title of safeScenarioNames) reporter.onTestEnd(fakeTest({ title }), fakeResult({ status: "passed" }));
    outcomes.push(reporter.onEnd({ status: "passed" } as FullResult));
  });
  capture((reporter) => {
    reporter.onBegin({} as FullConfig, fakeSuite(safeScenarioNames.map((title) => fakeTest({ title }))));
    outcomes.push(reporter.onEnd({ status: "passed" } as FullResult)); // discovery only
  });
  assert.deepEqual(await Promise.all(outcomes), [{ status: "failed" }, undefined, undefined]);
});
