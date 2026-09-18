import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { runManifest } from "./run-manifest";
import { retainedPass, signInThroughApi } from "./auth-helpers";

test("older content history remains reachable after restart without replacing the current draft", async ({ page }) => {
  test.setTimeout(90_000);
  await signInThroughApi(page);
  const auth = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": auth.csrf_token };
  const name = "E2E paged history " + runManifest.runGuid;
  let id: string;
  if (!retainedPass) {
    const template = await (await page.request.get(`/api/materials/${runManifest.state.content.id}`)).json();
    const createdBrand = await page.request.post("/api/brands", { headers, data: {
      company_id: runManifest.state.companyId, name, folder_prefix: "HISTORY", brand_identifier: "history_" + runManifest.runGuid.replaceAll("-", ""),
    } });
    expect(createdBrand.status()).toBe(201);
    const created = await page.request.post("/api/materials", { headers, data: {
      project_id: runManifest.state.projectId, published_brand_id: (await createdBrand.json()).id,
      assigned_processor_id: template.assigned_processor_id, material_name: name, main_category_code: "G03",
    } });
    expect(created.status()).toBe(201); id = (await created.json()).id;
    for (let revision = 1; revision <= 103; revision += 1) {
      const saved = await page.request.post(`/api/materials/${id}/content`, { headers, data: {
        idempotency_key: randomUUID(), expected_revision: revision - 1, description: `Synthetic revision ${revision}`,
        credits: revision, tags: [], category_ids: [], collection_ids: [], reason: "Verify bounded history navigation",
      } });
      expect(saved.status()).toBe(200);
      expect((await saved.json()).revision).toBe(revision);
    }
  } else {
    const materials = await page.request.get("/api/materials"); expect(materials.status()).toBe(200);
    const material = (await materials.json()).find((item: { material_name: string }) => item.material_name === name);
    expect(material).toBeTruthy(); id = material.id;
  }
  await page.goto(`/materials/${id}`);
  const panel = page.getByRole("article", { name: "Publication content", exact: true });
  await expect(panel.getByText("Revision 103 · Saved content")).toBeVisible();
  const history = panel.locator("details").filter({ has: page.locator("summary", { hasText: /^Content history$/ }) });
  await history.locator("summary").first().click();
  await history.getByRole("button", { name: "Load content history" }).click();
  await expect(history.getByText("Revision 103", { exact: true })).toBeVisible();
  await expect(history.getByText("Revision 4", { exact: true })).toBeVisible();
  await expect(history.getByText("Revision 3", { exact: true })).toHaveCount(0);
  const response = page.waitForResponse((item) => item.request().method() === "GET" && new URL(item.url()).pathname === `/api/materials/${id}/content-history` && new URL(item.url()).searchParams.has("after"));
  await history.getByRole("button", { name: "Older content history" }).click();
  expect((await response).status()).toBe(200);
  for (const revision of [3, 2, 1]) await expect(history.getByText(`Revision ${revision}`, { exact: true })).toBeVisible();
  await expect(history.getByRole("button", { name: "Older content history" })).toHaveCount(0);
  await expect(panel.getByRole("textbox", { name: "Description", exact: true })).toHaveValue("Synthetic revision 103");
  await expect(panel.getByRole("spinbutton", { name: "Credits", exact: true })).toHaveValue("103");
  await panel.screenshot({ path: test.info().outputPath("history-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await panel.screenshot({ path: test.info().outputPath("history-mobile.png") });
  await history.getByRole("button", { name: "Latest content history" }).click();
  await expect(history.getByText("Revision 103", { exact: true })).toBeVisible();
  await expect(history.getByText("Revision 1", { exact: true })).toHaveCount(0);
  const current = await (await page.request.get(`/api/materials/${id}/content`)).json();
  expect(current.revision).toBe(103);
});
