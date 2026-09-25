import { expect, test } from "@playwright/test";
import { cpSync, mkdirSync } from "node:fs";
import path from "node:path";
import { runManifest, e2eOutputDirectory } from "./run-manifest";
import { retainedPass, signInThroughApi } from "./auth-helpers";

test("material table edits, frozen bulk selection and lost-response recovery persist after restart", async ({ page }) => {
  await signInThroughApi(page);
  const session = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": session.csrf_token };
  const prefix = "Table workflow ";
  if (!retainedPass) {
    const template = await (await page.request.get(`/api/materials/${runManifest.state.valid.id}`)).json();
    const brandResult = await page.request.post("/api/brands", { headers, data: { company_id: runManifest.state.companyId,
      name: "Table acceptance", folder_prefix: "E2E_TABLE", brand_identifier: "e2e-table" } });
    expect(brandResult.status()).toBe(201); const brand = await brandResult.json();
    // runManifest has already checked the exact owned root, token and all root
    // path components. Copy only the runner's synthetic missing-metadata fixture.
    const targetRoot = path.join(runManifest.materialsRoot, "table-workflow");
    mkdirSync(targetRoot);
    for (const name of ["one", "two"]) {
      const created = await page.request.post("/api/materials", { headers, data: { project_id: runManifest.state.projectId,
        published_brand_id: brand.id, material_name: prefix + name, main_category_code: "K03", assigned_processor_id: template.assigned_processor_id } });
      expect(created.status()).toBe(201); const material = await created.json();
      expect(material.technical_identity).toMatch(/^E2E_TABLE_\d{4}_TABLE-WORKFLOW-(ONE|TWO)_K03$/);
      cpSync(path.join(runManifest.materialsRoot, runManifest.state.missing.relativePath), path.join(targetRoot, material.technical_identity), { recursive: true, errorOnExist: true, force: false });
      expect((await page.request.post(`/api/materials/${material.id}/folder-link`, { headers, data: { folder_path: `table-workflow/${material.technical_identity}` } })).status()).toBe(200);
    }
  }
  await page.goto("/materials"); await page.getByRole("button", { name: "List", exact: true }).click();
  await page.getByRole("searchbox", { name: "Search materials" }).fill(prefix);
  await expect(page.locator("tbody tr")).toHaveCount(2);
  const records = await (await page.request.get("/api/materials", { params: { search: prefix } })).json();
  expect(records).toHaveLength(2);
  const one = records.find((row: { material_name: string }) => row.material_name === prefix + "one");
  if (!retainedPass) {
    await page.getByRole("button", { name: "Select all filtered (2)" }).click();
    await page.getByRole("button", { name: "Review bulk change" }).click();
    const dialog = page.getByRole("dialog", { name: "Change 2 materials" });
    await dialog.getByRole("button", { name: "Apply change", exact: true }).click();
    await expect(dialog.getByText(/2 saved · 0 rejected/)).toBeVisible();
    await dialog.getByRole("button", { name: "Close report" }).click();
    for (const name of ["one", "two"]) await expect(page.getByRole("combobox", { name: `Status for ${prefix}${name}` })).toHaveValue("DONE");
    await page.getByRole("combobox", { name: `Checked for ${prefix}one` }).selectOption("OK");
    await expect(page.getByRole("combobox", { name: `Checked for ${prefix}one` })).toBeEnabled();
    await page.getByRole("combobox", { name: `Checked for ${prefix}one` }).selectOption("Correction");
    await expect(page.getByRole("combobox", { name: `Status for ${prefix}one` })).toHaveValue("IN_PROGRESS");
    await page.getByRole("combobox", { name: `Status for ${prefix}one` }).selectOption("DONE");
    await expect(page.getByRole("combobox", { name: `Checked for ${prefix}one` })).toHaveValue("no");
    await expect(page.getByRole("combobox", { name: `Status for ${prefix}one` })).toBeEnabled();
    let sentKey = "";
    const routePath = `**/api/materials/${one.id}/table`;
    await page.route(routePath, async route => {
      sentKey = route.request().headers()["idempotency-key"];
      const actual = await route.fetch(); expect(actual.status()).toBe(200); await route.abort("failed");
    });
    await page.getByRole("textbox", { name: `Note for ${prefix}one` }).fill("Checked texture\nKeep this note");
    await page.getByRole("button", { name: "Save note" }).click();
    await expect(page.getByRole("button", { name: "Retry same request", exact: true })).toBeVisible();
    await page.unroute(routePath);
    const replayPromise = page.waitForResponse(r => r.request().method() === "PATCH" && r.url().endsWith(`/materials/${one.id}/table`));
    await page.getByRole("button", { name: "Retry same request", exact: true }).click();
    const replay = await replayPromise;
    expect(replay.headers()["idempotency-replayed"]).toBe("true");
    expect(replay.request().headers()["idempotency-key"]).toBe(sentKey);
    await expect(page.getByRole("checkbox", { name: `Published for ${prefix}one` })).toBeEnabled();
    await page.getByRole("checkbox", { name: `Published for ${prefix}one` }).click();
    await expect(page.getByRole("checkbox", { name: `Published for ${prefix}one` })).toBeChecked();
    await expect(page.getByRole("checkbox", { name: `Published for ${prefix}one` })).toBeEnabled();
  }
  await page.reload(); await page.getByRole("searchbox", { name: "Search materials" }).fill(prefix);
  await expect(page.locator("tbody tr")).toHaveCount(2);
  await expect(page.getByRole("textbox", { name: `Note for ${prefix}one` })).toHaveValue("Checked texture\nKeep this note");
  await expect(page.getByRole("checkbox", { name: `Published for ${prefix}one` })).toBeChecked();
  await expect(page.getByRole("combobox", { name: `Checked for ${prefix}one` })).toHaveValue("no");
  await expect(page.getByRole("combobox", { name: `Category for ${prefix}one` })).toHaveValue("K03");
  const saved = await (await page.request.get(`/api/materials/${one.id}`)).json();
  expect(saved.workflow_status).toBe("DONE"); expect(saved.publication_status).toBe("NOT_PUBLISHED");
  expect((await (await page.request.get(`/api/materials/${one.id}/metadata/snapshots`)).json())).toHaveLength(2);
  const history = await (await page.request.get(`/api/materials/${one.id}/history`)).json();
  expect(history.items.filter((item: { after: { note?: string }; before: { note?: string } }) => item.after.note === "Checked texture\nKeep this note" && item.before.note !== item.after.note)).toHaveLength(1);
  await page.screenshot({ path: path.join(e2eOutputDirectory, "..", `material-table-${retainedPass ? "retained" : "fresh"}.png`), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
