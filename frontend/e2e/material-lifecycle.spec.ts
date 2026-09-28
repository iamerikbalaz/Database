import { expect, test } from "@playwright/test";
import { retainedPass, signInThroughApi } from "./auth-helpers";
import { runManifest } from "./run-manifest";

test("material archive survives restart and restore preserves identity and exact recovery", async ({ page }) => {
  await signInThroughApi(page);
  const auth = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": auth.csrf_token };
  const suffix = runManifest.runGuid.replaceAll("-", ""), name = "E2E lifecycle " + suffix;
  const materialName = "E2E-LIFECYCLE-" + suffix.toUpperCase();
  async function create(segment: string, data: Record<string, unknown>) {
    const response = await page.request.post(`/api/${segment}`, { headers, data });
    expect(response.status()).toBe(201); return response.json();
  }
  let id: string;
  if (!retainedPass) {
    const brand = await create("brands", { company_id: runManifest.state.companyId, name, folder_prefix: "LIFECYCLE", brand_identifier: "lifecycle_" + suffix });
    const project = await create("projects", { company_id: runManifest.state.companyId, name, project_number: "LIFECYCLE-" + suffix });
    const user = await create("internal-users", { display_name: name, email: `lifecycle-${suffix}@example.invalid`, role: "PROCESSOR" });
    id = (await create("materials", { project_id: project.id, published_brand_id: brand.id, assigned_processor_id: user.id,
      material_name: name, main_category_code: "G03" })).id;
  } else {
    const response = await page.request.get("/api/material-archives"); expect(response.status()).toBe(200);
    const item = (await response.json()).items.find((row: { material: { material_name: string } }) => row.material.material_name === materialName);
    expect(item).toBeTruthy(); id = item.material.id; expect(item.version).toBe(1); expect(item.is_archived).toBe(true);
  }
  const archivePath = `/api/material-archives/${id}`, materialPath = `/api/materials/${id}`;
  const original = (await (await page.request.get(archivePath)).json()).material;
  await page.goto(retainedPass ? `/material-archives/${id}` : `/materials/${id}`);
  const archived = page.getByRole("checkbox", { name: `Archived for ${materialName}`, exact: true });
  if (retainedPass) {
    const before = await (await page.request.get(archivePath + "/history")).json();
    expect(before.items.map((item: { version: number }) => item.version)).toEqual([1]);
    await archived.click(); await expect(archived).toBeEnabled(); await expect(archived).not.toBeChecked();
    const restored = await (await page.request.get(materialPath)).json();
    for (const field of ["id", "sequence_number", "technical_identity", "folder_path", "assigned_processor_id", "project_id", "published_brand_id", "workflow_status", "checked_status", "is_published"]) expect(restored[field]).toEqual(original[field]);
    const prior = before.items[0];
    const replay = await page.request.post(archivePath + "/commands", { headers, data: { action: "ARCHIVE", request_key: prior.request_key,
      expected_version: 0, expected_input_sha256: prior.input_sha256, reason: prior.reason, acknowledge: true } });
    expect(replay.status()).toBe(200); expect((await replay.json()).event).toEqual(prior);
    expect((await (await page.request.get(archivePath)).json()).is_archived).toBe(false);
  }
  const panel = page.getByRole("article", { name: "Material properties" });
  await expect(page.getByLabel("Reason for lifecycle change")).toHaveCount(0);
  const sent: string[] = [];
  await page.route("**" + archivePath + "/commands", async (route) => {
    sent.push(route.request().postData()!);
    const upstream = await route.fetch(); expect(upstream.status()).toBe(200);
    if (sent.length === 1) await route.abort("failed"); else await route.fulfill({ response: upstream });
  });
  await archived.click();
  await expect(panel.getByRole("alert")).toContainText("The outcome could not be verified");
  if (retainedPass) {
    await panel.getByRole("button", { name: "Retry exact lifecycle request", exact: true }).click();
    await expect.poll(() => sent.length).toBe(2); expect(sent[1]).toBe(sent[0]);
  } else {
    await panel.getByRole("button", { name: "Check saved lifecycle result", exact: true }).click();
  }
  await expect(archived).toBeChecked();
  await expect(archived).toBeEnabled();
  await expect(page.getByText("Archive date", { exact: true })).toBeVisible();
  await page.unroute("**" + archivePath + "/commands");
  expect(sent).toHaveLength(retainedPass ? 2 : 1);
  expect((await page.request.get(materialPath)).status()).toBe(404);
  expect((await (await page.request.get("/api/materials")).json()).some((item: { id: string }) => item.id === id)).toBe(false);
  const current = await (await page.request.get(archivePath)).json();
  expect(current.is_archived).toBe(true); expect(current.version).toBe(retainedPass ? 3 : 1);
  for (const field of ["workflow_status", "checked_status", "is_published"]) expect(current.material[field]).toEqual(original[field]);
  const history = await (await page.request.get(archivePath + "/history")).json();
  expect(history.items.map((item: { version: number }) => item.version)).toEqual(retainedPass ? [3, 2, 1] : [1]);
  expect(history.items.every((item: { actor_id: string }) => item.actor_id === auth.user.id)).toBe(true);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.locator("main").screenshot({ path: test.info().outputPath("material-archive-mobile.png") });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.locator("main").screenshot({ path: test.info().outputPath("material-archive-desktop.png") });
  await page.goto("/material-archives");
  await page.getByRole("searchbox", { name: "Search materials" }).fill(materialName);
  await expect(page.getByRole("table")).toBeVisible();
  await expect(page.getByRole("link", { name: materialName, exact: true })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Archive date", exact: true })).toBeVisible();
});
