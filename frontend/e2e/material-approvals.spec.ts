import { expect, test } from "@playwright/test";
import { runManifest } from "./run-manifest";
import { retainedPass, signInThroughApi } from "./auth-helpers";

test("real image checks and two human approvals survive the retained-data restart", async ({ page }) => {
  await signInThroughApi(page);
  const fixture = runManifest.state.approval; const path = `/api/materials/${fixture.id}`;
  const auth = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": auth.csrf_token };
  if (!retainedPass) {
    expect((await page.request.post(path + "/folder-link", { headers, data: { folder_path: fixture.relativePath } })).status()).toBe(200);
    expect((await page.request.post(path + "/mark-done", { headers })).status()).toBe(200);
  }
  await page.goto(`/materials/${fixture.id}`);
  await expect(page.getByRole("article", { name: "Technical review" })).toHaveCount(0);
  const initial = await (await page.request.get(path + "/technical-review")).json();
  const response = await page.request.post(path + "/technical-review/run", { headers, data: { idempotency_key: crypto.randomUUID(), expected_generation: initial.review.generation } });
  expect(response.status()).toBe(200); let current = await response.json();
  if (!retainedPass) {
    expect(current.validation.report.can_approve).toBe(true);
    expect(current.validation.report.warnings.some((item: { code: string }) => item.code === "SOURCE_METADATA_MISSING")).toBe(true);
    expect(current.validation.report.images.map((item: { width: number; height: number }) => [item.width, item.height])).toEqual([[1024, 1024], [1024, 1024], [1024, 1024]]);
    for (const kind of ["TECHNICAL", "PUBLICATION"]) {
      const approved = await page.request.post(path + "/approvals", { headers, data: { kind, expected_generation: current.review.generation,
        expected_revision_hash: current.review.revision_hash, technical_check_id: current.validation.id, idempotency_key: crypto.randomUUID(),
        note: "Synthetic test maps reviewed; metadata and previews may be added later.", warnings_acknowledged: true } });
      expect(approved.status()).toBe(200); current = await approved.json();
    }
  } else {
    expect(initial.approvals).toHaveLength(2);
    expect(current.review.generation).toBe(initial.review.generation);
    expect(current.review.revision_hash).toBe(initial.review.revision_hash);
    expect(current.approvals).toEqual(initial.approvals);
  }
  await page.reload();
  const stored = await (await page.request.get(path + "/technical-review")).json();
  expect(stored.validation.report.images).toHaveLength(3);
  expect(stored.approvals).toHaveLength(2);
  expect(stored.approvals.every((item: { revision_hash: string; generation: number }) => item.revision_hash === stored.review.revision_hash && item.generation === stored.review.generation)).toBe(true);
  const material = await (await page.request.get(path)).json();
  expect(material.workflow_status).toBe("DONE"); expect(material.is_published).toBe(false);
  expect(JSON.stringify(stored)).not.toMatch(/raw_content|source_content|e2e-materials/);
});
