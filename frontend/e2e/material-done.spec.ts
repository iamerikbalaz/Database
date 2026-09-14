import {
  expect,
  type Locator,
  type Page,
  type Response,
} from "@playwright/test";
import type { E2eAuthCredentials } from "./auth-credentials.ts";
import { test } from "./auth-fixture.ts";
import { endpointFromPath, markPhase, recordConsole, recordHttp, SafeE2eError, type SafePhase } from "./safe-diagnostics.ts";
import { runManifest, type MaterialFixture } from "./run-manifest";

type BrowserDiagnostics = {
  authenticationSecretLeak: boolean;
  consoleErrors: string[];
  expectedHttpErrors: Array<{ method: string; pathname: string; status: number }>;
  expectedSessionUnauthorizedResponses: number;
  nativeSessionUnauthorizedConsoleErrors: number;
  requestFailures: string[];
  responseInspections: Array<Promise<{
    pathLeak: boolean;
    authenticationSecretLeak: boolean;
    inspectionFailed: boolean;
  }>>;
  unexpectedHttpErrors: string[];
};

const materialsRoot = runManifest.materialsRoot;
const state = runManifest.state;
const diagnostics = new WeakMap<Page, BrowserDiagnostics>();
const frontendOrigin = new URL(runManifest.frontendUrl).origin;

function containsAuthenticationSecret(value: string, authCredentials: E2eAuthCredentials): boolean {
  return value.includes(authCredentials.initialPassword) || value.includes(authCredentials.password);
}

function isNativeSessionUnauthorizedConsoleError(message: {
  text(): string;
  location(): { url: string };
}): boolean {
  if (!/^Failed to load resource: the server responded with a status of 401(?: \([^\r\n]*\))?$/.test(message.text())) {
    return false;
  }
  try {
    const location = new URL(message.location().url);
    return location.origin === frontendOrigin && location.pathname === "/api/auth/session" &&
      location.search === "" && location.hash === "";
  } catch {
    return false;
  }
}

function isUnexpectedHttpError(
  observed: BrowserDiagnostics,
  method: string,
  pathname: string,
  status: number,
): boolean {
  if (status < 400) return false;
  const expectedIndex = observed.expectedHttpErrors.findIndex((expected) =>
    expected.method === method && expected.pathname === pathname && expected.status === status,
  );
  if (expectedIndex >= 0) {
    observed.expectedHttpErrors.splice(expectedIndex, 1);
    if (method === "GET" && pathname === "/api/auth/session" && status === 401) {
      observed.expectedSessionUnauthorizedResponses += 1;
    }
    return false;
  }
  return true;
}

function containsPathLeak(value: string): boolean {
  const normalizedRoot = materialsRoot.replaceAll("\\", "/");
  const escapedRoot = materialsRoot.replaceAll("\\", "\\\\");
  return (
    /raw_content|source_content/i.test(value) ||
    value.includes("/e2e-materials") ||
    value.includes(materialsRoot) ||
    value.includes(normalizedRoot) ||
    value.includes(escapedRoot) ||
    /(?:^|[\s"'])(?:[A-Za-z]:[\\/]|\\\\)[^\s"']+/.test(value)
  );
}

function materialFact(page: Page, label: string): Locator {
  return page.locator("dt", { hasText: new RegExp(`^${label}$`) }).locator("..").locator("dd");
}

function panel(page: Page, heading: string): Locator {
  return page.locator("article").filter({
    has: page.getByRole("heading", { name: heading, exact: true }),
  });
}

async function fillSecret(input: Locator, secret: string, phase: SafePhase = "login"): Promise<void> {
  try {
    await input.fill(secret);
  } catch {
    throw new SafeE2eError("E2E_PROTECTED_INPUT_FAILED", phase);
  }
}

function requestHasNonEmptyHeader(response: Response, name: string): boolean {
  const value = response.request().headers()[name.toLowerCase()];
  return typeof value === "string" && value.length > 0;
}

async function signIn(page: Page, authCredentials: E2eAuthCredentials, password = authCredentials.password): Promise<void> {
  markPhase(test.info(), "session");
  const observed = diagnostics.get(page);
  if (!observed) throw new Error("Browser diagnostics were not initialized.");
  observed.expectedHttpErrors.push({
    method: "GET",
    pathname: "/api/auth/session",
    status: 401,
  });
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  markPhase(test.info(), "login");
  await page.getByLabel("Email", { exact: true }).fill(authCredentials.email);
  await fillSecret(page.getByLabel("Password", { exact: true }), password);

  const loginResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" &&
    new URL(response.url()).pathname === "/api/auth/login",
  );
  const sessionResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "GET" &&
    new URL(response.url()).pathname === "/api/auth/session" &&
    response.status() === 200,
  );
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const loginResponse = await loginResponsePromise;
  expect(loginResponse.status()).toBe(200);
  expect(requestHasNonEmptyHeader(loginResponse, "x-csrf-token")).toBe(false);
  markPhase(test.info(), "session");
  const sessionResponse = await sessionResponsePromise;
  expect(sessionResponse.status()).toBe(200);
}

async function expectAnonymousServerSession(page: Page): Promise<void> {
  markPhase(test.info(), "session");
  const observed = diagnostics.get(page);
  if (!observed) throw new Error("Browser diagnostics were not initialized.");
  observed.expectedHttpErrors.push({
    method: "GET",
    pathname: "/api/auth/session",
    status: 401,
  });
  const status = await page.evaluate(async () => {
    const response = await fetch("/api/auth/session", { credentials: "include" });
    return response.status;
  });
  expect(status).toBe(401);
}

async function openPreparedMaterial(page: Page, material: MaterialFixture): Promise<void> {
  markPhase(test.info(), "assertion");
  await page.goto("/materials");
  await page.getByRole("link", { name: material.technical_identity, exact: true }).click();
  await expect(page.getByRole("heading", { name: material.material_name, exact: true })).toBeVisible();
}

async function checkFolder(page: Page, relativePath: string): Promise<Response> {
  await page.getByLabel("Relative folder path").fill(relativePath);
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" &&
    new URL(response.url()).pathname.endsWith("/folder-preflight"),
  );
  await page.getByRole("button", { name: "Check folder", exact: true }).click();
  return responsePromise;
}

async function confirmOperation(
  page: Page,
  trigger: string,
  dialogName: string,
  responseSuffix: string,
): Promise<Response> {
  await page.getByRole("button", { name: trigger, exact: true }).click();
  const dialog = page.getByRole("dialog", { name: dialogName, exact: true });
  await expect(dialog).toBeVisible();
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" &&
    new URL(response.url()).pathname.endsWith(responseSuffix),
  );
  await dialog.getByRole("button", { name: trigger, exact: true }).click();
  return responsePromise;
}

test.beforeEach(async ({ page, authCredentials }, testInfo) => {
  markPhase(testInfo, "assertion");
  const observed: BrowserDiagnostics = {
    authenticationSecretLeak: false,
    consoleErrors: [],
    expectedHttpErrors: [],
    expectedSessionUnauthorizedResponses: 0,
    nativeSessionUnauthorizedConsoleErrors: 0,
    requestFailures: [],
    responseInspections: [],
    unexpectedHttpErrors: [],
  };
  diagnostics.set(page, observed);
  page.on("console", (message) => {
    if (containsAuthenticationSecret(message.text(), authCredentials)) observed.authenticationSecretLeak = true;
    if (message.type() === "error" || message.type() === "warning") {
      if (message.type() === "error" && isNativeSessionUnauthorizedConsoleError(message)) {
        observed.nativeSessionUnauthorizedConsoleErrors += 1;
        return;
      }
      observed.consoleErrors.push(`${message.type()} output was observed`);
      recordConsole(testInfo, message.type() === "warning" ? "warning" : "error");
    }
  });
  page.on("pageerror", (error) => {
    if (containsAuthenticationSecret(error.message, authCredentials)) observed.authenticationSecretLeak = true;
    observed.consoleErrors.push("an uncaught page error was observed");
    recordConsole(testInfo, "pageerror");
  });
  page.on("request", (request) => {
    if (containsAuthenticationSecret(request.url(), authCredentials)) observed.authenticationSecretLeak = true;
  });
  page.on("requestfailed", (request) => {
    if (containsAuthenticationSecret(request.url(), authCredentials) ||
        containsAuthenticationSecret(request.failure()?.errorText ?? "", authCredentials)) {
      observed.authenticationSecretLeak = true;
    }
    observed.requestFailures.push("requestfailed");
    recordConsole(testInfo, "requestfailed");
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    const endpoint = endpointFromPath(url.pathname);
    if (endpoint) recordHttp(testInfo, endpoint, response.status());
    if (url.pathname.startsWith("/api/")) {
      observed.responseInspections.push(response.text().then((body) => ({
        pathLeak: containsPathLeak(body),
        authenticationSecretLeak: containsAuthenticationSecret(body, authCredentials),
        inspectionFailed: false,
      })).catch(() => ({
        pathLeak: false,
        authenticationSecretLeak: false,
        inspectionFailed: true,
      })));
    }
    if (isUnexpectedHttpError(
      observed,
      response.request().method(),
      url.pathname,
      response.status(),
    )) {
      observed.unexpectedHttpErrors.push("unexpected-http-status");
    }
  });
});

test.afterEach(async ({ page, authCredentials }) => {
  await page.locator('input[type="password"]').evaluateAll((elements) => {
    for (const element of elements) {
      (element as HTMLInputElement).value = "";
    }
  });
  const observed = diagnostics.get(page);
  expect(observed).toBeDefined();
  const responseInspections = await Promise.all(observed?.responseInspections ?? []);
  const exposedSensitiveValue = responseInspections.some((inspection) => inspection.pathLeak);
  const exposedAuthenticationSecret = responseInspections.some(
    (inspection) => inspection.authenticationSecretLeak,
  );
  expect(exposedSensitiveValue).toBe(false);
  expect(exposedAuthenticationSecret).toBe(false);
  expect(responseInspections.some((inspection) => inspection.inspectionFailed)).toBe(false);
  expect(observed?.authenticationSecretLeak).toBe(false);
  const visibleText = await page.locator("body").innerText();
  expect(containsPathLeak(visibleText)).toBe(false);
  expect(containsAuthenticationSecret(visibleText, authCredentials)).toBe(false);
  const folderInputs = await page.getByLabel("Relative folder path").evaluateAll((elements) =>
    elements.map((element) => (element as HTMLInputElement).value),
  );
  expect(folderInputs.some((value) =>
    value.includes(materialsRoot) || value.includes("/e2e-materials"),
  )).toBe(false);
  expect(observed?.consoleErrors).toEqual([]);
  expect(observed?.expectedHttpErrors).toEqual([]);
  expect(
    (observed?.nativeSessionUnauthorizedConsoleErrors ?? 0) <=
      (observed?.expectedSessionUnauthorizedResponses ?? 0),
  ).toBe(true);
  expect(observed?.requestFailures).toEqual([]);
  expect(observed?.unexpectedHttpErrors).toEqual([]);
});

test.describe.configure({ mode: "serial" });

test("required password change revokes the initial session and requires a new login", async ({ page, authCredentials }) => {
  await signIn(page, authCredentials, authCredentials.initialPassword);
  markPhase(test.info(), "change-password");
  await expect(page.getByRole("heading", { name: "Change password", exact: true })).toBeVisible();

  await fillSecret(
    page.getByLabel("Current password", { exact: true }),
    authCredentials.initialPassword,
    "change-password",
  );
  await fillSecret(page.getByLabel("New password", { exact: true }), authCredentials.password, "change-password");
  await fillSecret(
    page.getByLabel("Confirm new password", { exact: true }),
    authCredentials.password,
    "change-password",
  );
  const changeResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" &&
    new URL(response.url()).pathname === "/api/auth/change-password",
  );
  await page.getByRole("button", { name: "Change password", exact: true }).click();
  const changeResponse = await changeResponsePromise;
  expect(changeResponse.status()).toBe(200);
  expect(requestHasNonEmptyHeader(changeResponse, "x-csrf-token")).toBe(true);

  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await expect(page.getByText(
    "Your password was changed. Please sign in again with your new password.",
    { exact: true },
  )).toBeVisible();
  await expectAnonymousServerSession(page);
  await signIn(page, authCredentials);
  await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
});

test("login loads the server session and logout revokes it", async ({ page, authCredentials }) => {
  await signIn(page, authCredentials);
  await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "User menu", exact: true }).click();
  const accountDialog = page.getByRole("dialog", { name: "Your account", exact: true });
  await expect(accountDialog).toBeVisible();
  await expect(accountDialog.getByText("E2E QA", { exact: true })).toBeVisible();
  await expect(accountDialog.getByText(authCredentials.email, { exact: true })).toBeVisible();
  await expect(accountDialog.getByText("ADMIN", { exact: true })).toBeVisible();

  const logoutResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" &&
    new URL(response.url()).pathname === "/api/auth/logout",
  );
  markPhase(test.info(), "logout");
  await accountDialog.getByRole("button", { name: "Sign out", exact: true }).click();
  const logoutResponse = await logoutResponsePromise;
  expect(logoutResponse.status()).toBe(200);
  expect(requestHasNonEmptyHeader(logoutResponse, "x-csrf-token")).toBe(true);
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await expectAnonymousServerSession(page);
});

test("happy path persists Done metadata and snapshot after reload", async ({ page, authCredentials }) => {
  await signIn(page, authCredentials);
  markPhase(test.info(), "assertion");
  await page.getByRole("link", { name: "Companies", exact: true }).click();
  await page.getByRole("link", { name: "E2E Company", exact: true }).click();
  await expect(page.getByRole("heading", { name: "E2E Company", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "E2E Published Brand", exact: true }).click();
  await expect(page.getByRole("heading", { name: "E2E Published Brand", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await page.getByRole("link", { name: "E2E Disposable Project", exact: true }).click();
  await expect(page.getByRole("heading", { name: "E2E Disposable Project", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Materials", exact: true }).click();
  await expect(page).toHaveURL(/\/materials$/);
  await page.getByRole("link", { name: state.valid.technical_identity, exact: true }).click();
  await expect(page.getByRole("heading", { name: "E2E Valid Metadata" })).toBeVisible();
  await expect(materialFact(page, "Project")).toContainText("E2E Disposable Project");
  await expect(materialFact(page, "Published brand")).toContainText("E2E Published Brand");

  const preflightResponse = await checkFolder(page, state.valid.relativePath);
  expect(preflightResponse.status()).toBe(200);
  expect(await preflightResponse.json()).toMatchObject({
    identity_matches: true,
    can_continue: true,
    metadata_status: "VALID",
  });
  const preflight = page.getByLabel("Folder check result");
  await expect(preflight).toBeVisible();
  await expect(preflight.getByText("Yes", { exact: true })).toHaveCount(2);

  const linkResponse = await confirmOperation(
    page,
    "Link folder",
    "Link this folder?",
    "/folder-link",
  );
  expect(linkResponse.status()).toBe(200);
  await expect(materialFact(page, "Folder path")).toHaveText(state.valid.relativePath);

  const doneResponse = await confirmOperation(
    page,
    "Mark as Done",
    "Mark this material as Done?",
    "/mark-done",
  );
  expect(doneResponse.status()).toBe(200);
  expect(await doneResponse.json()).toMatchObject({
    material: { workflow_status: "DONE" },
    metadata: { status: "VALID" },
    snapshot: { sequence_number: 1, status: "VALID" },
  });
  await expect(materialFact(page, "Workflow status")).toHaveText("done");
  await expect(panel(page, "Current metadata").getByText("valid", { exact: true })).toBeVisible();
  await expect(panel(page, "Current metadata").getByText("#A1B2C3", { exact: true })).toBeVisible();
  await expect(panel(page, "Snapshot history").getByText("Snapshot 1", { exact: true })).toBeVisible();

  await page.reload();
  await expect(materialFact(page, "Workflow status")).toHaveText("done");
  await expect(materialFact(page, "Folder path")).toHaveText(state.valid.relativePath);
  await expect(panel(page, "Current metadata").getByText("valid", { exact: true })).toBeVisible();
  await expect(panel(page, "Snapshot history").getByText("Snapshot 1", { exact: true })).toBeVisible();
});

test("missing metadata remains non-blocking and its warning stays visible", async ({ page, authCredentials }) => {
  await signIn(page, authCredentials);
  await openPreparedMaterial(page, state.missing);
  const preflightResponse = await checkFolder(page, state.missing.relativePath);
  expect(preflightResponse.status()).toBe(200);
  expect(await preflightResponse.json()).toMatchObject({
    identity_matches: true,
    can_continue: true,
    metadata_status: "MISSING",
  });
  await expect(page.getByLabel("Folder check result").getByText("missing", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Link folder", exact: true })).toBeEnabled();

  expect((await confirmOperation(page, "Link folder", "Link this folder?", "/folder-link")).status()).toBe(200);
  await expect(page.getByRole("button", { name: "Mark as Done", exact: true })).toBeEnabled();
  expect((await confirmOperation(page, "Mark as Done", "Mark this material as Done?", "/mark-done")).status()).toBe(200);

  await expect(materialFact(page, "Workflow status")).toHaveText("done");
  const currentMetadata = panel(page, "Current metadata");
  await expect(currentMetadata.getByText("missing", { exact: true })).toBeVisible();
  await expect(currentMetadata.getByText(/SOURCE_METADATA_MISSING/)).toBeVisible();
  await expect(panel(page, "Snapshot history").getByText("Snapshot 1", { exact: true })).toBeVisible();
});

test("identity mismatch cannot be linked or marked Done", async ({ page, authCredentials }) => {
  await signIn(page, authCredentials);
  await openPreparedMaterial(page, state.mismatch);
  const preflightResponse = await checkFolder(page, state.mismatch.relativePath);
  expect(preflightResponse.status()).toBe(200);
  expect(await preflightResponse.json()).toMatchObject({
    identity_matches: false,
    can_continue: false,
  });
  const preflight = page.getByLabel("Folder check result");
  await expect(preflight.getByText("No", { exact: true })).toHaveCount(2);
  await expect(preflight.getByText(/TECHNICAL_IDENTITY_MISMATCH/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Link folder", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Mark as Done", exact: true })).toHaveCount(0);

  const materialResponse = await page.request.get(`/api/materials/${state.mismatch.id}`);
  expect(materialResponse.status()).toBe(200);
  expect(await materialResponse.json()).toMatchObject({
    folder_path: null,
    workflow_status: "IN_PROGRESS",
  });
});

test("client validation rejects absolute and traversal paths without HTTP", async ({ page, authCredentials }) => {
  await signIn(page, authCredentials);
  await openPreparedMaterial(page, state.mismatch);
  let preflightRequests = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname.endsWith("/folder-preflight")
    ) {
      preflightRequests += 1;
    }
  });
  const input = page.getByLabel("Relative folder path");

  await input.fill("C:/host/materials/secret");
  await page.getByRole("button", { name: "Check folder", exact: true }).click();
  await expect(input).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByText(/Drive paths, URLs and other URI-style paths/)).toBeVisible();
  expect(preflightRequests).toBe(0);

  await input.fill("library/../secret");
  await page.getByRole("button", { name: "Check folder", exact: true }).click();
  await expect(input).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByText(/must not contain/)).toBeVisible();
  expect(preflightRequests).toBe(0);
});
