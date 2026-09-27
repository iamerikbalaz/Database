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
  await expect(page.getByRole("article", { name: "Source review" })).toHaveCount(0);
  const scan = async () => {
    const before = await (await page.request.get(path + "/review")).json();
    const result = await page.request.post(path + "/inventory/scan", { headers, data: { idempotency_key: randomUUID(), expected_generation: before.generation } });
    expect(result.status()).toBe(200); return result.json();
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
    expect((await (await page.request.get(path + "/review")).json()).failure_code).toBe("MATERIAL_FIELDS_CHANGED");
    const changed = await scan();
    expect(changed.revision_hash).not.toBe(first.revision_hash);
    const response = await page.request.post(path + "/reopen", { headers, data: { idempotency_key: randomUUID(), expected_generation: changed.generation, reason: "Correct the source before technical review" } });
    expect(response.status()).toBe(200);
    await page.reload();
    await expect(page.getByRole("combobox", { name: "Status for E2E Reviewed Revision" })).toHaveValue("IN_PROGRESS");
    expect((await (await page.request.get(path + "/review")).json()).failure_code).toBe("REOPENED");
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
