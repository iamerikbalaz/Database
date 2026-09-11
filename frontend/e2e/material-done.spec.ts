import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import {
  expect,
  test,
  type APIRequestContext,
  type Locator,
  type Page,
  type Response,
} from "@playwright/test";

type Material = {
  id: string;
  technical_identity: string;
  material_name: string;
  folder_path: string | null;
  workflow_status: string;
};

type MaterialFixture = Material & { relativePath: string };

type SeedState = {
  companyId: string;
  brandId: string;
  projectId: string;
  valid: MaterialFixture;
  missing: MaterialFixture;
  mismatch: MaterialFixture;
};

type BrowserDiagnostics = {
  consoleErrors: string[];
  requestFailures: string[];
  responseBodies: Promise<string>[];
};

const backendURL = requiredEnvironment("E2E_BACKEND_URL");
const materialsRoot = requiredEnvironment("E2E_MATERIALS_ROOT");
const diagnostics = new WeakMap<Page, BrowserDiagnostics>();
let api: APIRequestContext;
let state: SeedState;

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set by scripts/test-demo-e2e.ps1.`);
  return value;
}

async function createRecord<T>(
  request: APIRequestContext,
  route: string,
  data: Record<string, unknown>,
): Promise<T> {
  const response = await request.post(route, { data });
  expect(response.status(), `POST ${route}`).toBe(201);
  return response.json() as Promise<T>;
}

async function createMaterialFixture(
  request: APIRequestContext,
  projectId: string,
  brandId: string,
  processorId: string,
  name: string,
): Promise<Material> {
  return createRecord<Material>(request, "/api/materials", {
    project_id: projectId,
    published_brand_id: brandId,
    material_name: name,
    main_category_code: "G03",
    assigned_processor_id: processorId,
  });
}

async function createMaterialDirectory(relativePath: string): Promise<void> {
  await mkdir(path.join(materialsRoot, ...relativePath.split("/"), "16K"), {
    recursive: true,
  });
}

async function seedIsolatedEnvironment(request: APIRequestContext): Promise<SeedState> {
  const company = await createRecord<{ id: string }>(request, "/api/companies", {
    name: "E2E Company",
    legal_name: "E2E Company (disposable)",
    country: "CZ",
    is_active: true,
  });
  const brand = await createRecord<{ id: string }>(request, "/api/brands", {
    company_id: company.id,
    name: "E2E Published Brand",
    folder_prefix: "E2E_SAFE",
    brand_identifier: "e2e-disposable-brand",
    is_active: true,
  });
  const project = await createRecord<{ id: string }>(request, "/api/projects", {
    company_id: company.id,
    project_number: "E2E-001",
    name: "E2E Disposable Project",
    status: "IN_PROGRESS",
  });
  const processor = await createRecord<{ id: string }>(
    request,
    "/api/internal-users",
    {
      display_name: "E2E Processor",
      email: "e2e.processor@example.invalid",
      role: "PROCESSOR",
      is_active: true,
    },
  );

  const validMaterial = await createMaterialFixture(
    request,
    project.id,
    brand.id,
    processor.id,
    "E2E Valid Metadata",
  );
  const missingMaterial = await createMaterialFixture(
    request,
    project.id,
    brand.id,
    processor.id,
    "E2E Missing Metadata",
  );
  const mismatchMaterial = await createMaterialFixture(
    request,
    project.id,
    brand.id,
    processor.id,
    "E2E Identity Mismatch",
  );

  const validPath = `e2e-library/${validMaterial.technical_identity}`;
  const missingPath = `e2e-library/${missingMaterial.technical_identity}`;
  const mismatchPath = "e2e-library/E2E_WRONG_FOLDER_G03";
  await createMaterialDirectory(validPath);
  await createMaterialDirectory(missingPath);
  await createMaterialDirectory(mismatchPath);
  await writeFile(
    path.join(materialsRoot, ...validPath.split("/"), "metadata.txt"),
    JSON.stringify({
      COLOR: { hex: "#A1B2C3" },
      TEXTURE_SIZE: { cm: { width: 12.5, height: 34 } },
    }),
    "utf8",
  );

  return {
    companyId: company.id,
    brandId: brand.id,
    projectId: project.id,
    valid: { ...validMaterial, relativePath: validPath },
    missing: { ...missingMaterial, relativePath: missingPath },
    mismatch: { ...mismatchMaterial, relativePath: mismatchPath },
  };
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

test.beforeAll(async ({ playwright }) => {
  api = await playwright.request.newContext({ baseURL: backendURL });
  state = await seedIsolatedEnvironment(api);
});

test.afterAll(async () => {
  await api.dispose();
});

test.beforeEach(async ({ page }) => {
  const observed: BrowserDiagnostics = {
    consoleErrors: [],
    requestFailures: [],
    responseBodies: [],
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
    if (new URL(response.url()).pathname.startsWith("/api/")) {
      observed.responseBodies.push(response.text().catch(() => ""));
    }
  });
});

test.afterEach(async ({ page }) => {
  const observed = diagnostics.get(page);
  expect(observed).toBeDefined();
  const responseBodies = await Promise.all(observed?.responseBodies ?? []);
  const normalizedRoot = materialsRoot.replaceAll("\\", "/");
  const escapedRoot = materialsRoot.replaceAll("\\", "\\\\");
  const exposedSensitiveValue = responseBodies.some((body) =>
    /raw_content|source_content/i.test(body) ||
    body.includes(materialsRoot) ||
    body.includes(normalizedRoot) ||
    body.includes(escapedRoot),
  );
  expect(exposedSensitiveValue).toBe(false);
  const visibleText = await page.locator("body").innerText();
  expect(/raw_content|source_content/i.test(visibleText)).toBe(false);
  expect(
    visibleText.includes(materialsRoot) || visibleText.includes(normalizedRoot),
  ).toBe(false);
  expect(observed?.consoleErrors).toEqual([]);
  expect(observed?.requestFailures).toEqual([]);
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

test("missing metadata remains non-blocking and its warning stays visible", async ({ page }) => {
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
