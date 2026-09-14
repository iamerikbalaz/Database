import type { TestInfo, TestStepInfo } from "@playwright/test";

// Inert during config loading and discovery. Everything crossing the reporter
// boundary has a closed vocabulary, independent of credentials or environment.
export const safeScenarioNames = [
  "required password change revokes the initial session and requires a new login",
  "login loads the server session and logout revokes it",
  "happy path persists Done metadata and snapshot after reload",
  "missing metadata remains non-blocking and its warning stays visible",
  "identity mismatch cannot be linked or marked Done",
  "client validation rejects absolute and traversal paths without HTTP",
] as const;

export const safePhases = [
  "discovery", "worker initialization", "login", "session", "change-password", "logout", "assertion",
] as const;
export type SafePhase = (typeof safePhases)[number];

export const safeErrorCodes = [
  "E2E_CREDENTIALS_MISSING", "E2E_CREDENTIALS_INVALID", "E2E_WORKER_INITIALIZATION_FAILED",
  "E2E_DISCOVERY_FAILED", "E2E_ASSERTION_FAILED", "E2E_TEST_TIMEOUT", "E2E_RUN_INTERRUPTED",
  "E2E_HTTP_FAILURE", "E2E_BROWSER_CONSOLE", "E2E_SECRET_EXPOSURE", "E2E_PROTECTED_INPUT_FAILED",
] as const;
export type SafeErrorCode = (typeof safeErrorCodes)[number];

export const safeEndpoints = [
  "/api/auth/login", "/api/auth/session", "/api/auth/change-password", "/api/auth/logout",
  "/api/companies", "/api/companies/{id}", "/api/brands", "/api/brands/{id}",
  "/api/projects", "/api/projects/{id}", "/api/internal-users", "/api/internal-users/{id}",
  "/api/materials", "/api/materials/{id}", "/api/materials/{id}/folder-preflight",
  "/api/materials/{id}/folder-link", "/api/materials/{id}/mark-done",
  "/api/materials/{id}/metadata", "/api/materials/{id}/metadata/snapshots",
] as const;
export type SafeEndpoint = (typeof safeEndpoints)[number];

export const safeConsoleCategories = ["error", "warning", "pageerror", "requestfailed"] as const;
export type SafeConsoleCategory = (typeof safeConsoleCategories)[number];

export const safeAnnotationTypes = {
  phase: "reawote-safe-phase", http: "reawote-safe-http", console: "reawote-safe-console",
} as const;

export function isAllowed<T extends string>(values: readonly T[], candidate: unknown): candidate is T {
  return typeof candidate === "string" && values.some((value) => value === candidate);
}

/** Only a pathname is accepted; hosts, query values and concrete IDs are never reported. */
export function endpointFromPath(pathname: string): SafeEndpoint | undefined {
  if (isAllowed(safeEndpoints, pathname)) return pathname;
  const normalized = pathname.replace(
    /^\/api\/(companies|brands|projects|internal-users|materials)\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?=\/|$)/i,
    "/api/$1/{id}",
  );
  return isAllowed(safeEndpoints, normalized) ? normalized : undefined;
}

type AnnotationTarget = Pick<TestInfo | TestStepInfo, "annotations">;

export function markPhase(target: AnnotationTarget, phase: SafePhase): void {
  if (isAllowed(safePhases, phase)) {
    target.annotations.push({ type: safeAnnotationTypes.phase, description: phase });
  }
}

export function recordHttp(target: AnnotationTarget, endpoint: SafeEndpoint, status: number): void {
  if (isAllowed(safeEndpoints, endpoint) && Number.isInteger(status) && status >= 100 && status <= 599) {
    target.annotations.push({ type: safeAnnotationTypes.http, description: `${endpoint} ${status}` });
  }
}

export function recordConsole(target: AnnotationTarget, category: SafeConsoleCategory): void {
  if (isAllowed(safeConsoleCategories, category)) {
    target.annotations.push({ type: safeAnnotationTypes.console, description: category });
  }
}

const errorMarker = "REAWOTE_SAFE_E2E:";

/** The marker survives worker serialization; it contains no caller-supplied text. */
export class SafeE2eError extends Error {
  constructor(code: SafeErrorCode, phase: SafePhase) {
    const safeCode = isAllowed(safeErrorCodes, code) ? code : "E2E_ASSERTION_FAILED";
    const safePhase = isAllowed(safePhases, phase) ? phase : "assertion";
    super(`${errorMarker}${safeCode}:${safePhase}`);
    this.name = "SafeE2eError";
  }
}

export function readSafeErrorMarker(message: unknown): { code: SafeErrorCode; phase: SafePhase } | undefined {
  // Never print the received message or scan exception text for a partial match.
  if (typeof message !== "string") return undefined;
  // Playwright serializes Error.name followed by ': '; accept only our exact name.
  const candidate = message.startsWith("SafeE2eError: ") ? message.slice("SafeE2eError: ".length) : message;
  if (!candidate.startsWith(errorMarker)) return undefined;
  const fields = candidate.slice(errorMarker.length).split(":");
  if (fields.length !== 2) return undefined;
  const [code, phase] = fields;
  return isAllowed(safeErrorCodes, code) && isAllowed(safePhases, phase) ? { code, phase } : undefined;
}

/** Sanitize the public errors array before Playwright writes error-context.md. */
export function sanitizeFailureDiagnostics(target: Pick<TestInfo, "errors" | "annotations" | "status">): void {
  let phase: SafePhase = "assertion";
  for (const annotation of target.annotations) {
    if (annotation.type === safeAnnotationTypes.phase && isAllowed(safePhases, annotation.description)) {
      phase = annotation.description;
    }
  }
  const fallback: SafeErrorCode = target.status === "timedOut" ? "E2E_TEST_TIMEOUT" :
    target.status === "interrupted" ? "E2E_RUN_INTERRUPTED" : "E2E_ASSERTION_FAILED";
  const errors = target.errors.map((error) => {
    const safe = readSafeErrorMarker(error.message);
    // A new object deliberately drops stack, value, nested causes, call logs and
    // errorContext. Preserve the failure count/status; never turn failure green.
    return { message: new SafeE2eError(safe?.code ?? fallback, safe?.phase ?? phase).message };
  });
  target.errors.splice(0, target.errors.length, ...errors);
}
