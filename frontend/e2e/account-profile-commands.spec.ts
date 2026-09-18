import { expect, test } from "@playwright/test";
import path from "node:path";
import { e2eOutputDirectory, runManifest } from "./run-manifest";
import { retainedPass, signInThroughApi } from "./auth-helpers";

test("profile creates and role changes recover lost responses without replacing later edits", async ({ page }) => {
  await signInThroughApi(page);
  const session = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": session.csrf_token };
  const name = "E2E profile recovery " + runManifest.runGuid, email = "recovery." + runManifest.runGuid + "@example.invalid";
  // Distinct from the material scenario and each other, deterministic across
  // restarts, and still UUIDv4. Only the browser's test-generated key is fixed.
  const keyFor = (value: number) => (parseInt(runManifest.runGuid[0], 16) ^ value).toString(16) + runManifest.runGuid.slice(1);
  const createKey = keyFor(1), updateKey = keyFor(2);
  if (!retainedPass) {
    await page.addInitScript((key) => { Object.defineProperty(crypto, "randomUUID", { configurable: true, value: () => key }); }, createKey);
    let creates = 0;
    await page.route("**/api/internal-users", async (route) => {
      if (route.request().method() !== "POST") { await route.continue(); return; }
      creates += 1; expect(route.request().headers()["idempotency-key"]).toBe(createKey);
      const response = await route.fetch(); expect(response.status()).toBe(201); await route.abort("failed");
    });
    await page.goto("/settings/users");
    let form = page.getByRole("form", { name: "Create account", exact: true });
    await form.getByLabel("Display name", { exact: true }).fill(name);
    await form.getByLabel("Email", { exact: true }).fill(email);
    await form.getByRole("button", { name: "Create profile", exact: true }).click();
    await expect(form.getByRole("button", { name: "Check saved result" })).toBeVisible();
    await form.screenshot({ path: path.join(e2eOutputDirectory, "..", "profile-create-recovery-desktop.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await form.screenshot({ path: path.join(e2eOutputDirectory, "..", "profile-create-recovery-mobile.png") });
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.getByRole("link", { name: "Companies", exact: true }).click();
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    form = page.getByRole("form", { name: "Create account", exact: true });
    await expect(form.getByLabel("Email", { exact: true })).toHaveValue(email);
    await expect(form.getByLabel("Email", { exact: true })).toBeDisabled();
    await form.getByRole("button", { name: "Check saved result" }).click();
    const row = page.getByRole("form", { name: "Manage " + name, exact: true });
    await expect(row).toBeVisible(); await expect(form.getByRole("button", { name: "Check saved result" })).toHaveCount(0);
    expect(creates).toBe(1); await page.unroute("**/api/internal-users");
    const receipt = await (await page.request.get(`/api/resource-commands/${createKey}`)).json();
    const target = receipt.resource_id;
    await page.evaluate((key) => { Object.defineProperty(crypto, "randomUUID", { configurable: true, value: () => key }); }, updateKey);
    let updates = 0;
    await page.route(`**/api/internal-users/${target}`, async (route) => {
      if (route.request().method() !== "PATCH") { await route.continue(); return; }
      updates += 1; expect(route.request().headers()["idempotency-key"]).toBe(updateKey);
      const response = await route.fetch(); expect(response.status()).toBe(200);
      if (updates === 1) { await route.abort("failed"); return; }
      expect(response.headers()["idempotency-replayed"]).toBe("true"); await route.fulfill({ response });
    });
    await row.getByRole("combobox", { name: "Role", exact: true }).selectOption("PRODUCTION_LEAD");
    await row.getByLabel("Active", { exact: true }).uncheck();
    await row.getByRole("button", { name: "Save role and status", exact: true }).click();
    await expect(row.getByRole("button", { name: "Retry exact save" })).toBeVisible();
    await expect(form.getByRole("button", { name: "Create profile", exact: true })).toBeDisabled();
    await row.screenshot({ path: path.join(e2eOutputDirectory, "..", "profile-update-recovery-desktop.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await row.screenshot({ path: path.join(e2eOutputDirectory, "..", "profile-update-recovery-mobile.png") });
    await page.setViewportSize({ width: 1280, height: 900 });
    // An intentional later administration change must survive the old replay.
    expect((await page.request.patch(`/api/internal-users/${target}`, { headers, data: { role: "LEADERSHIP", is_active: true } })).status()).toBe(200);
    await row.getByRole("button", { name: "Retry exact save" }).click();
    await expect(row.getByRole("combobox", { name: "Role", exact: true })).toHaveValue("LEADERSHIP");
    await expect(row.getByLabel("Active", { exact: true })).toBeChecked(); expect(updates).toBe(2);
  }
  const createReceipt = await page.request.get(`/api/resource-commands/${createKey}`); expect(createReceipt.status()).toBe(200);
  const original = (await createReceipt.json()).response;
  const updateReceipt = await page.request.get(`/api/resource-commands/${updateKey}`); expect(updateReceipt.status()).toBe(200);
  const changed = (await updateReceipt.json()).response;
  expect(changed.id).toBe(original.id); expect(changed.role).toBe("PRODUCTION_LEAD"); expect(changed.is_active).toBe(false);
  const replay = await page.request.post("/api/internal-users", { headers: { ...headers, "Idempotency-Key": createKey },
    data: { display_name: name, email, role: "PROCESSOR" } });
  expect(replay.status()).toBe(201); expect(replay.headers()["idempotency-replayed"]).toBe("true"); expect(await replay.json()).toEqual(original);
  const editReplay = await page.request.patch(`/api/internal-users/${original.id}`, { headers: { ...headers, "Idempotency-Key": updateKey },
    data: { role: "PRODUCTION_LEAD", is_active: false } });
  expect(editReplay.status()).toBe(200); expect(editReplay.headers()["idempotency-replayed"]).toBe("true"); expect(await editReplay.json()).toEqual(changed);
  const current = await (await page.request.get(`/api/internal-users/${original.id}`)).json();
  expect(current.role).toBe("LEADERSHIP"); expect(current.is_active).toBe(true);
  const found = await page.request.get("/api/internal-users", { params: { search: email } }); expect(found.status()).toBe(200);
  expect(await found.json()).toHaveLength(1);
});
