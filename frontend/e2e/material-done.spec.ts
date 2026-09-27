import {
  expect,
  test,
  type Locator,
  type Page,
  type Response,
  type APIResponse,
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
  return page.getByRole("article", { name: "Material properties" }).locator("dt", { hasText: new RegExp(`^${label}$`) }).locator("..").locator("dd");
}

async function openPreparedMaterial(page: Page, material: MaterialFixture): Promise<void> {
  await page.goto("/materials");
  await page.getByRole("link", { name: material.technical_identity, exact: true }).click();
  await expect(page.getByRole("heading", { name: material.material_name, exact: true })).toBeVisible();
}

async function assertRetainedDone(page: Page, material: MaterialFixture, status: string) {
  await expect(materialFact(page, "Status").getByRole("combobox")).toHaveValue("DONE");
  await expect(materialFact(page, "Folder path")).toHaveText(material.relativePath);
  const snapshots = await page.request.get(`/api/materials/${material.id}/metadata/snapshots`);
  expect(snapshots.status()).toBe(200);
  expect(await snapshots.json()).toMatchObject([{ sequence_number: 1, status }]);
  expect((await snapshots.json()).length).toBe(1);
  await waitForMaterialReads(page);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Snapshot history", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Mark as Done", exact: true })).toHaveCount(0);
}

async function waitForMaterialReads(page: Page): Promise<void> {
  await expect(page.getByRole("article", { name: "Publication content", exact: true }).getByText(/^Revision \d+ ·/)).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Color HEX", exact: true })).toBeVisible();
  await expect(page.getByRole("article", { name: "Material properties" })).toBeVisible();
}

async function setDone(page: Page): Promise<Response> {
  const response = page.waitForResponse(item => item.request().method() === "PATCH" && new URL(item.url()).pathname.endsWith("/table"));
  await materialFact(page, "Status").getByRole("combobox").selectOption("DONE");
  const saved = await response;
  await expect(materialFact(page, "Status").getByRole("combobox")).toBeEnabled();
  return saved;
}

async function savedMetadata(page: Page, id: string, status: string) {
  const metadata = await (await page.request.get(`/api/materials/${id}/metadata`)).json();
  const snapshots = await (await page.request.get(`/api/materials/${id}/metadata/snapshots`)).json();
  expect(metadata.status).toBe(status); expect(snapshots).toHaveLength(1); expect(snapshots[0]).toMatchObject({ sequence_number: 1, status });
  return metadata;
}

async function folderRequest(page: Page, material: MaterialFixture, action: "folder-preflight" | "folder-link", relativePath = material.relativePath): Promise<APIResponse> {
  const session = await (await page.request.get("/api/auth/session")).json();
  return page.request.post(`/api/materials/${material.id}/${action}`, {
    headers: { Origin: runManifest.frontendUrl, "X-CSRF-Token": session.csrf_token }, data: { folder_path: relativePath },
  });
}

async function linkFixture(page: Page, material: MaterialFixture): Promise<void> {
  expect((await folderRequest(page, material, "folder-link")).status()).toBe(200);
  await waitForMaterialReads(page);
  await page.reload();
  await expect(materialFact(page, "Folder path")).toHaveText(material.relativePath);
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
    // Navigating away intentionally cancels near-viewport list thumbnails.
    if (request.method() === "GET" && /^\/api\/materials\/[^/]+\/previews?$/.test(new URL(request.url()).pathname) && request.failure()?.errorText === "net::ERR_ABORTED") return;
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

  await expect(page.getByRole("heading", { name: "Material data folder", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Browse source folders", exact: true })).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("material-card-mobile.png") });
  await page.setViewportSize({ width: 1440, height: 1000 });

  if (retainedPass) { await assertRetainedDone(page, state.valid, "VALID"); return; }
  const preflightResponse = await folderRequest(page, state.valid, "folder-preflight");
  expect(preflightResponse.status()).toBe(200);
  expect(await preflightResponse.json()).toMatchObject({
    identity_matches: true,
    can_continue: true,
    metadata_status: "VALID",
  });
  await linkFixture(page, state.valid);

  const doneResponse = await setDone(page);
  expect(doneResponse.status()).toBe(200);
  expect(await doneResponse.json()).toMatchObject({ workflow_status: "DONE" });
  expect(await savedMetadata(page, state.valid.id, "VALID")).toMatchObject({ hex_color: "#A1B2C3" });
  await expect(materialFact(page, "Status").getByRole("combobox")).toHaveValue("DONE");
  await expect(page.getByRole("combobox", { name: "Color HEX", exact: true })).toHaveValue("#A1B2C3");

  await waitForMaterialReads(page);
  await page.reload();
  await expect(materialFact(page, "Status").getByRole("combobox")).toHaveValue("DONE");
  await expect(materialFact(page, "Folder path")).toHaveText(state.valid.relativePath);
  await savedMetadata(page, state.valid.id, "VALID");
  await expect(page.getByRole("heading", { name: "Snapshot history", exact: true })).toHaveCount(0);
});

test("missing metadata remains non-blocking and its warning persists", async ({ page }) => {
  await openPreparedMaterial(page, state.missing);
  if (retainedPass) { await assertRetainedDone(page, state.missing, "MISSING"); return; }
  const preflightResponse = await folderRequest(page, state.missing, "folder-preflight");
  expect(preflightResponse.status()).toBe(200);
  expect(await preflightResponse.json()).toMatchObject({
    identity_matches: true,
    can_continue: true,
    metadata_status: "MISSING",
  });
  await linkFixture(page, state.missing);
  expect((await setDone(page)).status()).toBe(200);

  await expect(materialFact(page, "Status").getByRole("combobox")).toHaveValue("DONE");
  const metadata = await savedMetadata(page, state.missing.id, "MISSING");
  expect(metadata.warnings.some((item: { code: string }) => item.code === "SOURCE_METADATA_MISSING")).toBe(true);
  await expect(page.getByRole("combobox", { name: "Color HEX", exact: true })).toHaveValue("");
});

test("unsupported metadata dimensions stay nonblocking across worker, API and PostgreSQL", async ({ page }) => {
  await openPreparedMaterial(page, state.dimensions);
  if (retainedPass) { await assertRetainedDone(page, state.dimensions, "WARNING"); return; }
  const preflight = await folderRequest(page, state.dimensions, "folder-preflight");
  expect(preflight.status()).toBe(200);
  expect(await preflight.json()).toMatchObject({ metadata_status: "WARNING", width_cm: null, height_cm: "2", can_continue: true });
  await linkFixture(page, state.dimensions);
  const done = await setDone(page); expect(done.status()).toBe(200);
  expect(await done.json()).toMatchObject({ workflow_status: "DONE" });
  const metadata = await savedMetadata(page, state.dimensions.id, "WARNING");
  expect(metadata).toMatchObject({ width_cm: null, height_cm: "2.0000" });
  expect(metadata.warnings.some((item: { code: string }) => item.code === "SOURCE_METADATA_DIMENSION_UNSUPPORTED")).toBe(true);
});

test("identity mismatch cannot be linked or marked Done", async ({ page }) => {
  await openPreparedMaterial(page, state.mismatch);
  const preflightResponse = await folderRequest(page, state.mismatch, "folder-preflight");
  expect(preflightResponse.status()).toBe(200);
  expect(await preflightResponse.json()).toMatchObject({
    identity_matches: false,
    can_continue: false,
  });
  expect((await preflightResponse.json()).errors).toEqual(expect.arrayContaining([expect.objectContaining({ code: "TECHNICAL_IDENTITY_MISMATCH" })]));
  expect((await folderRequest(page, state.mismatch, "folder-link")).status()).toBe(409);
  const session = await (await page.request.get("/api/auth/session")).json();
  expect((await page.request.post(`/api/materials/${state.mismatch.id}/mark-done`, {
    headers: { Origin: runManifest.frontendUrl, "X-CSRF-Token": session.csrf_token },
  })).status()).toBe(409);
  await expect(page.getByRole("button", { name: "Mark as Done", exact: true })).toHaveCount(0);

  const materialResponse = await page.request.get(`/api/materials/${state.mismatch.id}`);
  expect(materialResponse.status()).toBe(200);
  expect(await materialResponse.json()).toMatchObject({
    folder_path: null,
    workflow_status: "IN_PROGRESS",
  });
});

test("folder API rejects absolute and traversal paths before linking", async ({ page }) => {
  await openPreparedMaterial(page, state.mismatch);
  for (const path of ["C:/host/materials/secret", "library/../secret"]) {
    for (const action of ["folder-preflight", "folder-link"] as const) {
      const response = await folderRequest(page, state.mismatch, action, path);
      expect(response.status()).toBe(422);
      expect(await response.text()).not.toContain(runManifest.materialsRoot);
    }
  }
  const material = await (await page.request.get(`/api/materials/${state.mismatch.id}`)).json();
  expect(material).toMatchObject({ folder_path: null, workflow_status: "IN_PROGRESS" });
  await expect(page.getByLabel("Relative folder path")).toHaveCount(0);
});
