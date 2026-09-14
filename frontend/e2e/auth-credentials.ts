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

function readRequiredEnvironment(name: (typeof environmentNames)[number]): string {
  const value = process.env[name];
  if (!value) {
    throw new Error("The supported E2E runner did not provide ephemeral authentication credentials.");
  }
  return value;
}

let email: string;
let initialPassword: string;
let password: string;
try {
  email = readRequiredEnvironment("E2E_AUTH_EMAIL");
  initialPassword = readRequiredEnvironment("E2E_AUTH_INITIAL_PASSWORD");
  password = readRequiredEnvironment("E2E_AUTH_PASSWORD");
} finally {
  for (const name of environmentNames) {
    delete process.env[name];
  }
}

if (!/^e2e\.admin\.[0-9a-f]{32}@example\.invalid$/.test(email)) {
  throw new Error("The supported E2E runner provided an invalid synthetic administrator identity.");
}
if (!/^E2E![0-9a-f]{64}$/.test(initialPassword) || !/^E2E![0-9a-f]{64}$/.test(password)) {
  throw new Error("The supported E2E runner provided an invalid synthetic password format.");
}
if (initialPassword === password) {
  throw new Error("The supported E2E runner must provide distinct initial and replacement passwords.");
}

export const authCredentials: E2eAuthCredentials = Object.freeze({
  email,
  initialPassword,
  password,
});
