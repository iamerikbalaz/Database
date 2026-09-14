import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
  TestCase,
  TestResult,
} from "@playwright/test/reporter";

function writeLine(value: string): void {
  process.stdout.write(`${value}\n`);
}

class SafeReporter implements Reporter {
  private failures = 0;

  printsToStdio(): boolean {
    return true;
  }

  onBegin(_config: FullConfig, suite: Suite): void {
    const tests = suite.allTests();
    writeLine(`Running ${tests.length} isolated browser scenarios.`);
    for (const test of tests) writeLine(`SCENARIO: ${test.title}`);
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    const label = test.title;
    if (result.status === "passed") {
      writeLine(`PASS: ${label}`);
      return;
    }
    if (result.status === "skipped") {
      writeLine(`SKIP: ${label}`);
      return;
    }
    this.failures += 1;
    // Never print Playwright errors, call logs, request data or attachments:
    // any of them could contain a password, cookie or CSRF token.
    writeLine(`FAIL: ${label} (details suppressed; inspect sanitized runner diagnostics)`);
  }

  onError(): void {
    this.failures += 1;
    writeLine("FAIL: Playwright reported an error (details suppressed)");
  }

  onEnd(result: FullResult): void {
    writeLine(`Browser scenarios finished with status ${result.status}; ${this.failures} failure(s).`);
  }
}

export default SafeReporter;
