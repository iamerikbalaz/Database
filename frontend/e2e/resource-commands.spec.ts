import { expect, test } from "@playwright/test";
import path from "node:path";
import { e2eOutputDirectory, runManifest } from "./run-manifest";
import { retainedPass, signInThroughApi } from "./auth-helpers";

test("a lost material-create response recovers exactly once and survives restart", async ({ page }) => {
  await signInThroughApi(page);
  const auth = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": auth.csrf_token };
  const name = "E2E recovered material " + runManifest.runGuid, key = runManifest.runGuid;
  let id: string;
  if (!retainedPass) {
    // Use the runner's unique UUID as this one browser command key so the second
    // pass can verify the same receipt after a full browser/application restart.
    await page.addInitScript((value) => { Object.defineProperty(crypto, "randomUUID", { value: () => value }); }, key);
    const template = await (await page.request.get(`/api/materials/${runManifest.state.content.id}`)).json();
    const brand = await page.request.post("/api/brands", { headers, data: { company_id: runManifest.state.companyId,
      name, folder_prefix: "RECOVERY", brand_identifier: "recovery_" + key.replaceAll("-", "") } });
    expect(brand.status()).toBe(201); const brandId = (await brand.json()).id;
    let sent = 0;
    await page.route("**/api/materials", async (route) => {
      if (route.request().method() !== "POST") { await route.continue(); return; }
      sent += 1; expect(route.request().headers()["idempotency-key"]).toBe(key);
      const actual = await route.fetch(); expect(actual.status()).toBe(201);
      await route.abort("failed");
    });
    await page.goto("/materials/new");
    await page.getByLabel("Project *", { exact: true }).selectOption(runManifest.state.projectId);
    await page.getByLabel("Published brand *", { exact: true }).selectOption(brandId);
    await page.getByLabel("Material name *", { exact: true }).fill(name);
    await page.getByLabel("Main category *", { exact: true }).fill("G03");
    await page.getByLabel("Processor *", { exact: true }).selectOption(template.assigned_processor_id);
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Check saved result" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    await page.getByRole("link", { name: "Cancel", exact: true }).click();
    await page.getByRole("link", { name: "Add material", exact: true }).click();
    await expect(page.getByLabel("Material name *", { exact: true })).toHaveValue(name);
    await expect(page.getByLabel("Material name *", { exact: true })).toBeDisabled();
    expect(sent).toBe(1);
    // This scenario only enters UNKNOWN on the fresh pass. Keep its screenshots
    // in the runner-owned artifact root: Playwright clears its own output on the
    // retained pass, which intentionally does not create a second material.
    await page.getByRole("region", { name: "Pending save" }).screenshot({ path: path.join(e2eOutputDirectory, "..", "save-recovery-desktop.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole("region", { name: "Pending save" }).screenshot({ path: path.join(e2eOutputDirectory, "..", "save-recovery-mobile.png") });
    await page.getByRole("button", { name: "Check saved result" }).click();
    await expect(page).toHaveURL(/\/materials\/[a-f0-9-]{36}$/);
    id = new URL(page.url()).pathname.split("/").at(-1)!;
    expect(sent).toBe(1); await page.unroute("**/api/materials");
    expect((await page.request.patch(`/api/materials/${id}`, { headers, data: { material_name: name + " later" } })).status()).toBe(200);
  } else {
    const found = await page.request.get("/api/materials", { params: { search: name } }); expect(found.status()).toBe(200);
    const materials = await found.json(); expect(materials).toHaveLength(1); id = materials[0].id;
  }
  const recovered = await page.request.get(`/api/resource-commands/${key}`); expect(recovered.status()).toBe(200);
  const receipt = await recovered.json(); expect(receipt.resource_id).toBe(id); expect(receipt.response.material_name).toBe(name);
  const original = receipt.response;
  const replay = await page.request.post("/api/materials", { headers: { ...headers, "Idempotency-Key": key }, data: {
    project_id: original.project_id, published_brand_id: original.published_brand_id, assigned_processor_id: original.assigned_processor_id,
    material_name: name, main_category_code: "G03",
  } });
  expect(replay.status()).toBe(201); expect(replay.headers()["idempotency-replayed"]).toBe("true");
  expect(await replay.json()).toEqual(original);
  const current = await (await page.request.get(`/api/materials/${id}`)).json(); expect(current.material_name).toBe(name + " later");
  expect((await (await page.request.get(`/api/brands/${original.published_brand_id}`)).json()).next_sequence_number).toBe(2);
  expect(await (await page.request.get("/api/materials", { params: { search: name } })).json()).toHaveLength(1);
});
