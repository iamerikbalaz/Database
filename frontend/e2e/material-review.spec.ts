import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { runManifest } from "./run-manifest";
import { retainedPass, signInThroughApi } from "./auth-helpers";

test("source inventory, revision invalidation and reopen persist with audit history", async ({ page }) => {
  await signInThroughApi(page);
  const fixture = runManifest.state.review;
  const path = `/api/materials/${fixture.id}`;
  const auth = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": auth.csrf_token };
  if (!retainedPass) {
    expect((await page.request.post(path + "/folder-link", { headers, data: { folder_path: fixture.relativePath } })).status()).toBe(200);
    expect((await page.request.post(path + "/mark-done", { headers })).status()).toBe(200);
  }
  await page.goto(`/materials/${fixture.id}`);
  const panel = page.getByRole("article", { name: "Source review" });
  await expect(panel.getByRole("button", { name: "Scan source inventory" })).toBeEnabled();
  const scan = async () => {
    const response = page.waitForResponse((item) => item.request().method() === "POST" && new URL(item.url()).pathname === path + "/inventory/scan");
    await panel.getByRole("button", { name: "Scan source inventory" }).click();
    const result = await response;
    expect(result.status()).toBe(200);
    await expect(panel.getByText("Observed revision available", { exact: true })).toBeVisible();
    return result.json();
  };
  if (retainedPass) {
    const prior = await (await page.request.get(path + "/review")).json();
    expect(prior.revision_hash).toMatch(/^[a-f0-9]{64}$/);
    expect(prior.generation).toBeGreaterThanOrEqual(5);
    const current = await scan();
    expect(current.generation).toBe(prior.generation);
    expect(current.revision_hash).toBe(prior.revision_hash);
    await expect(page.getByRole("button", { name: "Reopen material", exact: true })).toHaveCount(0);
  } else {
    const first = await scan();
    const inventory = await (await page.request.get(path + "/inventory")).json();
    expect(inventory.inventory.entries).toEqual([{ path: "16K", kind: "directory", size: 0, sha256: null }]);
    expect(JSON.stringify(inventory)).not.toMatch(/raw_content|source_content|e2e-materials/);
    expect((await page.request.patch(path, { headers, data: { material_name: "E2E Reviewed Revision" } })).status()).toBe(200);
    await page.reload();
    await expect(panel.locator("dl").getByText("MATERIAL_FIELDS_CHANGED", { exact: true })).toBeVisible();
    const changed = await scan();
    expect(changed.revision_hash).not.toBe(first.revision_hash);
    await panel.getByRole("button", { name: "Reopen material", exact: true }).click();
    await panel.getByLabel("Reason for reopening").fill("Correct the source before technical review");
    const response = page.waitForResponse((item) => item.request().method() === "POST" && new URL(item.url()).pathname === path + "/reopen");
    await panel.getByRole("button", { name: "Confirm reopen" }).click();
    expect((await response).status()).toBe(200);
    await expect(page.getByRole("button", { name: "Mark as Done", exact: true })).toBeVisible();
    await expect(panel.locator("dl").getByText("REOPENED", { exact: true })).toBeVisible();
    const reopened = await scan();
    expect(reopened.generation).toBeGreaterThan(changed.generation);
    const stale = await page.request.post(path + "/inventory/scan", { headers, data: { idempotency_key: randomUUID(), expected_generation: first.generation } });
    expect(stale.status()).toBe(409);
  }
  await page.reload();
  const stored = await (await page.request.get(path)).json();
  expect(stored.workflow_status).toBe("IN_PROGRESS");
  const snapshots = await (await page.request.get(path + "/metadata/snapshots")).json();
  expect(snapshots).toHaveLength(1);
  expect(snapshots[0].sequence_number).toBe(1);
  const metadata = await (await page.request.get(path + "/metadata")).json();
  expect(metadata.status).toBe("NOT_SCANNED");
  expect(metadata.current_snapshot_id).toBeNull();
  const audit = await (await page.request.get(path + "/audit")).json();
  expect(audit.filter((item: { event_type: string }) => item.event_type === "REOPENED")).toHaveLength(1);
});
