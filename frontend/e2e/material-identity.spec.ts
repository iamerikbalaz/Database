import { expect, test } from "@playwright/test";
import { runManifest } from "./run-manifest";
import { retainedPass, signInThroughApi } from "./auth-helpers";

test("confirmed rebrand renames real source files and survives backend and worker restart", async ({ page }) => {
  await signInThroughApi(page);
  const fixture = runManifest.state.identity; const path = `/api/materials/${fixture.id}`;
  const session = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": session.csrf_token };
  if (!retainedPass) expect((await page.request.post(path + "/folder-link", { headers, data: { folder_path: fixture.relativePath } })).status()).toBe(200);
  await page.goto(`/materials/${fixture.id}`);
  const panel = page.getByRole("article", { name: "Material identity" });
  if (!retainedPass) {
    await panel.getByRole("combobox", { name: "Target brand", exact: true }).selectOption(runManifest.state.identityBrandId);
    await panel.getByLabel("Target category code").fill("G04");
    await panel.getByRole("button", { name: "Preview identity changes" }).click();
    await expect(panel.getByRole("region", { name: "Identity change preview" })).toBeVisible();
    await expect(panel.getByText("E2E_NEXT_0001_G04", { exact: true })).toBeVisible();
    await expect(panel.getByText(/Confirmation reserves number 1/)).toBeVisible();
    await panel.getByLabel("Reason for identity change").fill("Synthetic E2E rebrand and category correction");
    const response = page.waitForResponse((item) => item.request().method() === "POST" && new URL(item.url()).pathname === path + "/identity-confirm");
    await panel.getByRole("button", { name: "Confirm identity change" }).click();
    const confirmed = await response;
    expect(confirmed.status()).toBe(200); expect((await confirmed.json()).status).toBe("COMPLETED");
  }
  await expect(page.getByRole("link", { name: "E2E Identity Target", exact: true })).toBeVisible();
  const current = await (await page.request.get(path)).json();
  expect(current.id).toBe(fixture.id); expect(current.project_id).toBe(runManifest.state.projectId);
  expect(current.published_brand_id).toBe(runManifest.state.identityBrandId);
  expect(current.technical_identity).toBe("E2E_NEXT_0001_G04");
  expect(current.folder_path).toBe("e2e-identity/E2E_NEXT_0001_G04");
  expect(current.workflow_status).toBe("IN_PROGRESS");
  const operations = await (await page.request.get(path + "/identity-operations")).json();
  expect(operations.operations).toHaveLength(1); expect(operations.operations[0].status).toBe("COMPLETED");
  const review = await (await page.request.get(path + "/review")).json();
  const scan = await page.request.post(path + "/inventory/scan", { headers, data: { expected_generation: review.generation, idempotency_key: crypto.randomUUID() } });
  expect(scan.status()).toBe(200);
  const inventory = await (await page.request.get(path + "/inventory")).json();
  expect(inventory.inventory.entries.filter((entry: { path: string }) => entry.path.includes(fixture.technical_identity))).toHaveLength(0);
  expect(inventory.inventory.entries.filter((entry: { path: string }) => entry.path.includes("E2E_NEXT_0001_G04"))).toHaveLength(4);
  expect(inventory.inventory.source_revision_hash).toBe(operations.operations[0].result.target_revision_hash);
  expect((await (await page.request.get(path + "/identity-history")).json())).toHaveLength(1);
  const brand = await (await page.request.get("/api/brands/" + runManifest.state.identityBrandId)).json();
  expect(brand.next_sequence_number).toBe(2);
  expect((await page.request.post(path + "/identity-operations/" + operations.operations[0].id + "/resume", { headers })).status()).toBe(200);
});
