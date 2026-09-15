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
  const panel = page.getByRole("article", { name: "Technical review" });
  await expect(panel.getByRole("button", { name: "Run technical checks" })).toBeEnabled();
  const initial = await (await page.request.get(path + "/technical-review")).json();
  if (!retainedPass) {
    const response = page.waitForResponse((item) => item.request().method() === "POST" && new URL(item.url()).pathname === path + "/technical-review/run");
    await panel.getByRole("button", { name: "Run technical checks" }).click();
    expect((await response).status()).toBe(200);
    await expect(panel.getByText("Passed with warnings", { exact: true })).toBeVisible();
    await expect(panel.getByRole("region", { name: "Technical warnings" })).toContainText("Root metadata.txt is missing");
    await panel.getByText("Verified maps (3)", { exact: true }).click();
    await expect(panel.getByRole("cell", { name: "1024 × 1024" })).toHaveCount(3);
    for (const label of ["Approve technically", "Approve for publication"]) {
      const approve = panel.getByRole("button", { name: label, exact: true });
      await expect(approve).toBeDisabled();
      await panel.getByLabel("Approval note", { exact: true }).fill("Synthetic test maps reviewed; metadata and previews may be added later.");
      await panel.getByRole("checkbox").check();
      const approved = page.waitForResponse((item) => item.request().method() === "POST" && new URL(item.url()).pathname === path + "/approvals");
      await approve.click(); expect((await approved).status()).toBe(200);
    }
  } else {
    expect(initial.approvals).toHaveLength(2);
    const response = page.waitForResponse((item) => item.request().method() === "POST" && new URL(item.url()).pathname === path + "/technical-review/run");
    await panel.getByRole("button", { name: "Run technical checks" }).click();
    const rechecked = await response; expect(rechecked.status()).toBe(200);
    const current = await rechecked.json();
    expect(current.review.generation).toBe(initial.review.generation);
    expect(current.review.revision_hash).toBe(initial.review.revision_hash);
    expect(current.approvals).toEqual(initial.approvals);
  }
  await page.reload();
  await expect(panel.getByText("Approved for this revision", { exact: true })).toHaveCount(2);
  const stored = await (await page.request.get(path + "/technical-review")).json();
  expect(stored.validation.report.images).toHaveLength(3);
  expect(stored.approvals).toHaveLength(2);
  expect(stored.approvals.every((item: { revision_hash: string; generation: number }) => item.revision_hash === stored.review.revision_hash && item.generation === stored.review.generation)).toBe(true);
  const material = await (await page.request.get(path)).json();
  expect(material.workflow_status).toBe("DONE"); expect(material.is_published).toBe(false);
  expect(JSON.stringify(stored)).not.toMatch(/raw_content|source_content|e2e-materials/);
});
