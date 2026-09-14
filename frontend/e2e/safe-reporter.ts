import path from "node:path";
import { fileURLToPath } from "node:url";
import type {
  FullConfig, FullResult, Reporter, Suite, TestCase, TestError, TestResult, TestStep,
} from "@playwright/test/reporter";
import {
  isAllowed, readSafeErrorMarker, safeAnnotationTypes, safeConsoleCategories,
  safeEndpoints, safePhases, safeScenarioNames,
  type SafeErrorCode, type SafePhase,
} from "./safe-diagnostics.ts";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const safeFiles = [
  "frontend/e2e/material-done.spec.ts", "frontend/e2e/auth-credentials.ts",
  "frontend/e2e/auth-fixture.ts", "frontend/e2e/safe-diagnostics.ts",
  "frontend/e2e/run-manifest.ts", "frontend/playwright.config.ts",
] as const;

function safeLocation(location: TestCase["location"] | undefined): string | undefined {
  if (!location || typeof location.file !== "string" || !Number.isInteger(location.line) ||
      location.line < 1 || location.line > 100_000) return undefined;
  // Exact files only: no basename matching, prefix containment, ../ normalization,
  // arbitrary filename, source snippets or stack traces can get into the report.
  for (const relative of safeFiles) {
    if (location.file === relative || location.file === path.join(repositoryRoot, ...relative.split("/")) ||
        location.file === `${repositoryRoot.replaceAll("\\", "/")}/${relative}`) {
      return `${relative}:${location.line}`;
    }
  }
  return undefined;
}

type Annotations = TestResult["annotations"];
function phaseFrom(annotations: Annotations): SafePhase | undefined {
  let phase: SafePhase | undefined;
  for (const annotation of annotations) {
    if (annotation.type === safeAnnotationTypes.phase && isAllowed(safePhases, annotation.description)) {
      phase = annotation.description;
    }
  }
  return phase;
}

function safeEvents(annotations: Annotations): string[] {
  const events = new Set<string>();
  for (const annotation of annotations) {
    if (annotation.type === safeAnnotationTypes.console &&
        isAllowed(safeConsoleCategories, annotation.description)) {
      events.add(`CONSOLE: ${annotation.description}`);
    }
    if (annotation.type === safeAnnotationTypes.http && typeof annotation.description === "string") {
      const fields = annotation.description.split(" ");
      if (fields.length === 2 && isAllowed(safeEndpoints, fields[0]) && /^[1-5][0-9]{2}$/.test(fields[1])) {
        events.add(`HTTP: ${fields[0]} ${fields[1]}`);
      }
    }
  }
  return [...events];
}

function labelOf(test: TestCase): string {
  return isAllowed(safeScenarioNames, test.title) ? test.title : "unrecognized scenario";
}

function writeLine(value: string): void {
  process.stdout.write(`${value}\n`);
}

class SafeReporter implements Reporter {
  private failures = 0;
  private began = false;
  private completed = 0;
  private passed = new Set<string>();
  private skipped = 0;
  private failedSteps = new WeakMap<TestResult, { location?: string; phase?: SafePhase }>();

  printsToStdio(): boolean {
    return true;
  }

  onBegin(_config: FullConfig, suite: Suite): void {
    this.began = true;
    const tests = suite.allTests();
    writeLine(`Running ${tests.length} isolated browser scenarios.`);
    for (const test of tests) writeLine(`SCENARIO: ${labelOf(test)}`);
  }

  onStepEnd(_test: TestCase, result: TestResult, step: TestStep): void {
    // Never inspect or print the step title/error details.
    if (step.error && !this.failedSteps.has(result)) {
      this.failedSteps.set(result, {
        location: safeLocation(step.location),
        phase: phaseFrom(step.annotations) ?? phaseFrom(result.annotations),
      });
    }
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    this.completed++;
    const label = labelOf(test);
    if (result.status === "passed") {
      if (isAllowed(safeScenarioNames, test.title)) this.passed.add(test.title);
      writeLine(`PASS: ${label}`);
      return;
    }
    if (result.status === "skipped") {
      this.skipped++;
      writeLine(`SKIP: ${label}`);
      return;
    }
    this.failures += 1;
    // Only our exact closed-vocabulary marker is decoded. Arbitrary Error.message,
    // stack, call logs, stdout/stderr, attachments, headers and bodies are not emitted.
    const marker = result.errors.map((error) => readSafeErrorMarker(error.message)).find(Boolean);
    const failedStep = this.failedSteps.get(result);
    const code: SafeErrorCode = marker?.code ?? (result.status === "timedOut" ? "E2E_TEST_TIMEOUT" :
      result.status === "interrupted" ? "E2E_RUN_INTERRUPTED" : "E2E_ASSERTION_FAILED");
    const phase = marker?.phase ?? failedStep?.phase ?? phaseFrom(result.annotations) ?? "assertion";
    const location = failedStep?.location ?? safeLocation(test.location);
    writeLine(`FAIL: ${label}; code=${code}; phase=${phase}${location ? `; at=${location}` : ""}`);
    for (const event of safeEvents(result.annotations)) writeLine(event);
    this.failedSteps.delete(result);
  }

  onError(error: TestError): void {
    this.failures += 1;
    const marker = readSafeErrorMarker(error.message);
    const code = marker?.code ?? (this.began ? "E2E_WORKER_INITIALIZATION_FAILED" : "E2E_DISCOVERY_FAILED");
    const phase = marker?.phase ?? (this.began ? "worker initialization" : "discovery");
    const location = safeLocation(error.location);
    writeLine(`FAIL: infrastructure; code=${code}; phase=${phase}${location ? `; at=${location}` : ""}`);
  }

  onEnd(result: FullResult): Promise<{ status: "failed" } | undefined> {
    // --list has no test-result events. A real run must finish every one of the
    // six allowlisted scenarios; an apparently green partial/skip run is refused.
    const incomplete = this.completed > 0 && (this.completed !== safeScenarioNames.length ||
      this.passed.size !== safeScenarioNames.length || this.skipped !== 0 || this.failures !== 0);
    if (incomplete) {
      writeLine(`FAIL: infrastructure; code=E2E_SCENARIO_GATE_FAILED; phase=assertion; passed=${this.passed.size}; skipped=${this.skipped}; expected=6`);
      if (this.failures === 0) this.failures++;
    }
    const status = !incomplete && isAllowed(["passed", "failed", "timedout", "interrupted"] as const, result.status)
      ? result.status : "failed";
    writeLine(`Browser scenarios finished with status ${status}; ${this.failures} failure(s).`);
    return Promise.resolve(incomplete ? { status: "failed" } : undefined);
  }
}

export default SafeReporter;
