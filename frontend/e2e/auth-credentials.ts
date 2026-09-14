import { SafeE2eError } from "./safe-diagnostics.ts";

export type E2eAuthCredentials = Readonly<{
  email: string;
  initialPassword: string;
  password: string;
}>;

const environmentNames = [
  "E2E_AUTH_EMAIL",
  "E2E_AUTH_INITIAL_PASSWORD",
  "E2E_AUTH_PASSWORD",
] as const;

// Import/discovery is inert. Only the worker-scoped fixture calls this function.
// No cached singleton: every worker receives its own copy from its inherited env.
export function loadWorkerAuthCredentials(): E2eAuthCredentials {
  const environment = process.env;
  try {
    const email = environment.E2E_AUTH_EMAIL;
    const initialPassword = environment.E2E_AUTH_INITIAL_PASSWORD;
    const password = environment.E2E_AUTH_PASSWORD;
    if (!email || !initialPassword || !password) {
      throw new SafeE2eError("E2E_CREDENTIALS_MISSING", "worker initialization");
    }
    if (!/^e2e\.admin\.[0-9a-f]{32}@example\.invalid$/.test(email) ||
        !/^E2E![0-9a-f]{64}$/.test(initialPassword) ||
        !/^E2E![0-9a-f]{64}$/.test(password) || initialPassword === password) {
      throw new SafeE2eError("E2E_CREDENTIALS_INVALID", "worker initialization");
    }
    return Object.freeze({ email, initialPassword, password });
  } finally {
    // Also scrub partial/invalid input on failure. The controller is unaffected.
    for (const name of environmentNames) delete environment[name];
  }
}

const excludedBrowserEnvironment = new Set<string>([
  ...environmentNames, "POSTGRES_PASSWORD", "E2E_RUN_MANIFEST", "E2E_RUN_TOKEN",
]);

export function browserEnvironment(environment: NodeJS.ProcessEnv): Record<string, string> {
  const result: Record<string, string> = {};
  for (const name of Object.keys(environment)) {
    // Never even read a credential value while constructing the browser copy.
    if (excludedBrowserEnvironment.has(name.toUpperCase())) continue;
    const value = environment[name];
    if (value !== undefined) result[name] = value;
  }
  return result;
}
