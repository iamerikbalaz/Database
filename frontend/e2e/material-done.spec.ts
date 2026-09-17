import {
  expect,
  test,
  type Locator,
  type Page,
  type Response,
} from "@playwright/test";
import { runManifest, type MaterialFixture } from "./run-manifest";
import { retainedPass, signInThroughApi } from "./auth-helpers";

type BrowserDiagnostics = {
  consoleErrors: string[];
  requestFailures: string[];
  responseBodies: { path: string; body: Promise<string>; settled: boolean }[];
  unexpectedHttpErrors: string[];
};

const materialsRoot = runManifest.materialsRoot;
const state = runManifest.state;
const diagnostics = new WeakMap<Page, BrowserDiagnostics>();

const expectedHttpErrors: ReadonlyArray<{ method: string; pathname: string; status: number }> = [];

function isUnexpectedHttpError(method: string, pathname: string, status: number): boolean {
  if (status < 400) return false;
  return !expectedHttpErrors.some((expected) =>
    expected.method === method && expected.pathname === pathname && expected.status === status,
  );
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

async function openPreparedMaterial(page: Page, material: MaterialFixture): Promise<void> {
  await page.goto("/materials");
  await page.getByRole("link", { name: material.technical_identity, exact: true }).click();
  await expect(page.getByRole("heading", { name: material.material_name, exact: true })).toBeVisible();
}

async function assertRetainedDone(page: Page, material: MaterialFixture, status: string) {
  await expect(materialFact(page, "Workflow status")).toHaveText("done");
  await expect(materialFact(page, "Folder path")).toHaveText(material.relativePath);
  const snapshots = await page.request.get(`/api/materials/${material.id}/metadata/snapshots`);
  expect(snapshots.status()).toBe(200);
  expect(await snapshots.json()).toMatchObject([{ sequence_number: 1, status }]);
  expect((await snapshots.json()).length).toBe(1);
  await waitForMaterialReads(page);
  await page.reload();
  await expect(panel(page, "Snapshot history").getByText("Snapshot 1", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Mark as Done", exact: true })).toHaveCount(0);
}

async function waitForMaterialReads(page: Page): Promise<void> {
  // A reload must exercise a fully loaded detail, including the independently
  // fetched panels. Reloading after only the metadata panel can abort another
  // panel's response between its headers and body.
  await expect(page.getByRole("article", { name: "Publication content", exact: true }).getByText(/^Revision \d+ ·/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Reload identity status", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reload content approval", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reload technical review", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reload source review", exact: true })).toBeVisible();
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

test.beforeEach(async ({ page }) => {
  await signInThroughApi(page);
  const observed: BrowserDiagnostics = {
    consoleErrors: [],
    requestFailures: [],
    responseBodies: [],
    unexpectedHttpErrors: [],
  };
  diagnostics.set(page, observed);
  page.on("console", (message) => {
    if (message.type() === "error" || message.type() === "warning") {
      observed.consoleErrors.push(`${message.type()}: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => observed.consoleErrors.push(`pageerror: ${error.message}`));
  page.on("requestfailed", (request) => {
    observed.requestFailures.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText}`);
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/api/") && !url.pathname.startsWith("/api/auth/")) {
      const captured = { path: response.request().method() + " " + url.pathname, body: Promise.resolve(""), settled: false };
      captured.body = response.text().catch(() => "").finally(() => { captured.settled = true; });
      observed.responseBodies.push(captured);
    }
    if (isUnexpectedHttpError(response.request().method(), url.pathname, response.status())) {
      observed.unexpectedHttpErrors.push(`${response.request().method()} ${url.pathname} -> ${response.status()}`);
    }
  });
});

test.afterEach(async ({ page }) => {
  const observed = diagnostics.get(page);
  expect(observed).toBeDefined();
  await waitForMaterialReads(page);
  const captured = observed?.responseBodies ?? [];
  let timer: ReturnType<typeof setTimeout> | undefined;
  const responseBodies = await Promise.race([
    Promise.all(captured.map((item) => item.body)),
    new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error("Incomplete API response bodies: " + captured.filter((item) => !item.settled).map((item) => item.path).join(", "))), 8000);
    }),
  ]).finally(() => clearTimeout(timer));
  const exposedSensitiveValue = responseBodies.some(containsPathLeak);
  expect(exposedSensitiveValue).toBe(false);
  const visibleText = await page.locator("body").innerText();
  expect(containsPathLeak(visibleText)).toBe(false);
  const folderInputs = await page.getByLabel("Relative folder path").evaluateAll((elements) =>
    elements.map((element) => (element as HTMLInputElement).value),
  );
  expect(folderInputs.some((value) =>
    value.includes(materialsRoot) || value.includes("/e2e-materials"),
  )).toBe(false);
  expect({
    consoleErrors: observed?.consoleErrors,
    requestFailures: observed?.requestFailures,
    unexpectedHttpErrors: observed?.unexpectedHttpErrors,
  }).toEqual({ consoleErrors: [], requestFailures: [], unexpectedHttpErrors: [] });
});

test("happy path persists Done metadata and snapshot after reload", async ({ page }) => {
  await page.goto("/");
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

  // Explicit one-level source discovery is a read-only aid. Verify both the
  // fresh and retained-data passes before using the normal preflight/link flow.
  await page.getByRole("button", { name: "Browse source folders", exact: true }).click();
  await page.getByRole("button", { name: "List folders", exact: true }).click();
  const discovery = page.getByRole("region", { name: "Browse source folders", exact: true });
  await expect(discovery.getByRole("status")).toContainText("source root");
  const parentParts = state.valid.relativePath.split("/").slice(0, -1);
  for (const part of parentParts) {
    await discovery.getByRole("button", { name: `Open folder ${part}`, exact: true }).click();
    await expect(discovery.getByRole("status")).toContainText("folders in");
  }
  await expect(discovery.getByRole("listitem").filter({ hasText: state.valid.technical_identity })).toContainText("Exact identity match");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await discovery.screenshot({ path: test.info().outputPath("folder-discovery-mobile.png") });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await discovery.screenshot({ path: test.info().outputPath("folder-discovery.png") });
  await discovery.getByRole("button", { name: "Use this folder", exact: true }).click();
  await expect(page.getByLabel("Relative folder path", { exact: true })).toHaveValue(state.valid.relativePath);
  const afterBrowse = await page.request.get(`/api/materials/${state.valid.id}`);
  expect(afterBrowse.status()).toBe(200);
  expect((await afterBrowse.json()).folder_path).toBe(retainedPass ? state.valid.relativePath : null);

  if (retainedPass) { await assertRetainedDone(page, state.valid, "VALID"); return; }
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

  await waitForMaterialReads(page);
  await page.reload();
  await expect(materialFact(page, "Workflow status")).toHaveText("done");
  await expect(materialFact(page, "Folder path")).toHaveText(state.valid.relativePath);
  await expect(panel(page, "Current metadata").getByText("valid", { exact: true })).toBeVisible();
  await expect(panel(page, "Snapshot history").getByText("Snapshot 1", { exact: true })).toBeVisible();
});

test("missing metadata remains non-blocking and its warning stays visible", async ({ page }) => {
  await openPreparedMaterial(page, state.missing);
  if (retainedPass) { await assertRetainedDone(page, state.missing, "MISSING"); return; }
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

test("unsupported metadata dimensions stay nonblocking across worker, API and PostgreSQL", async ({ page }) => {
  await openPreparedMaterial(page, state.dimensions);
  if (retainedPass) { await assertRetainedDone(page, state.dimensions, "WARNING"); return; }
  const preflight = await checkFolder(page, state.dimensions.relativePath);
  expect(preflight.status()).toBe(200);
  expect(await preflight.json()).toMatchObject({ metadata_status: "WARNING", width_cm: null, height_cm: "2", can_continue: true });
  expect((await confirmOperation(page, "Link folder", "Link this folder?", "/folder-link")).status()).toBe(200);
  const done = await confirmOperation(page, "Mark as Done", "Mark this material as Done?", "/mark-done");
  expect(done.status()).toBe(200);
  expect(await done.json()).toMatchObject({ material: { workflow_status: "DONE" },
    metadata: { status: "WARNING", width_cm: null, height_cm: "2.0000" }, snapshot: { sequence_number: 1 } });
  await expect(panel(page, "Current metadata").getByText(/SOURCE_METADATA_DIMENSION_UNSUPPORTED/)).toBeVisible();
});

test("identity mismatch cannot be linked or marked Done", async ({ page }) => {
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

test("client validation rejects absolute and traversal paths without HTTP", async ({ page }) => {
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
