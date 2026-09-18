import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { runManifest } from "./run-manifest";
import { retainedPass, signInThroughApi } from "./auth-helpers";

test("approved CSV batch, exact retry and frozen download survive retained restart", async ({ page }) => {
  test.setTimeout(90_000);
  await signInThroughApi(page);
  const fixture = runManifest.state.publication, path = `/api/materials/${fixture.id}`;
  const auth = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": auth.csrf_token };
  if (!retainedPass) {
    expect((await page.request.post(path + "/folder-link", { headers, data: { folder_path: fixture.relativePath } })).status()).toBe(200);
    expect((await page.request.post(path + "/mark-done", { headers })).status()).toBe(200);
    const category = await page.request.post("/api/online-categories", { headers, data: { idempotency_key: crypto.randomUUID(), value: "E2E Export Stone" } });
    expect(category.status()).toBe(201);
    expect((await page.request.post(path + "/content", { headers, data: { idempotency_key: crypto.randomUUID(), expected_revision: 0,
      description: "Synthetic approved export", credits: 12, tags: ["matte"], category_ids: [(await category.json()).id], collection_ids: [], reason: "Prepare synthetic export" } })).status()).toBe(200);
    const review = await (await page.request.get(path + "/review")).json();
    const check = await page.request.post(path + "/technical-review/run", { headers, data: { idempotency_key: crypto.randomUUID(), expected_generation: review.generation } });
    expect(check.status()).toBe(200); let current = await check.json();
    for (const kind of ["TECHNICAL", "PUBLICATION"]) {
      const decision = await page.request.post(path + "/approvals", { headers, data: { idempotency_key: crypto.randomUUID(),
        expected_generation: current.review.generation, expected_revision_hash: current.review.revision_hash, technical_check_id: current.validation.id,
        kind, warnings_acknowledged: true, note: "Reviewed synthetic files and accepted missing optional previews/source" } });
      expect(decision.status()).toBe(200); current = await decision.json();
    }
    const content = await (await page.request.get(path + "/content-review")).json();
    expect((await page.request.post(path + "/content/approve", { headers, data: { idempotency_key: crypto.randomUUID(), expected_revision: content.content_revision,
      expected_context_hash: content.context_hash, warnings_acknowledged: true, note: "Reviewed synthetic publication content" } })).status()).toBe(200);
  }
  if (!retainedPass) {
    await page.goto("/materials/" + fixture.id);
    const policy = page.getByRole("article", { name: "ZIP packaging policy", exact: true });
    await policy.locator("summary").click();
    await expect(policy.getByText("No ZIP policy has been saved.", { exact: true })).toBeVisible();
    await policy.getByLabel("Reason for ZIP policy decision").fill("Freeze original synthetic ZIP dates");
    await policy.getByRole("checkbox", { name: "I reviewed this ZIP rule and its effect.", exact: true }).check();
    await policy.getByRole("button", { name: "Save ZIP policy", exact: true }).click();
    await expect(policy.getByText("ZIP policy saved.", { exact: true })).toBeVisible();
    await expect(policy.getByText("Current · retain packaging dates", { exact: true })).toBeVisible();
  }
  await page.goto("/publication");
  if (!retainedPass) {
    await page.getByRole("searchbox", { name: "Search materials", exact: true }).fill(fixture.material_name);
    await page.getByRole("button", { name: "Find materials", exact: true }).click();
    await page.getByRole("checkbox", { name: `${fixture.material_name} ${fixture.technical_identity}`, exact: true }).check();
    await page.getByRole("button", { name: "Review selected materials", exact: true }).click();
    await expect(page.getByText("All selected materials passed the current approval checks.", { exact: true })).toBeVisible();
    const previewPanel = page.getByRole("group", { name: "2. Review export values", exact: true });
    await expect(previewPanel).toContainText("12.5x34 cm");
    await previewPanel.screenshot({ path: test.info().outputPath("publication-preview.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await previewPanel.screenshot({ path: test.info().outputPath("publication-preview-mobile.png") });
    await page.getByLabel("Reason for preparing this batch").fill("Freeze synthetic approved CSV");
    await page.getByRole("checkbox", { name: "I reviewed the selected materials and export values.", exact: true }).check();
    let discarded = false; const sent: unknown[] = [];
    await page.route("**/api/publication-batches", async (route) => {
      if (route.request().method() !== "POST") { await route.continue(); return; }
      sent.push(route.request().postDataJSON());
      if (!discarded) { discarded = true; const committed = await route.fetch(); expect(committed.status()).toBe(201); await route.abort("failed"); }
      else await route.continue();
    });
    await page.getByRole("button", { name: "Save CSV batch", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("The outcome is unknown");
    await expect(page.getByLabel("Reason for preparing this batch")).toBeDisabled();
    await page.getByRole("button", { name: "Retry same batch request", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("CSV batch saved");
    expect(sent).toHaveLength(2); expect(sent[0]).toEqual(sent[1]);
    await page.unroute("**/api/publication-batches");
    const batchPanel = page.getByRole("group", { name: "Saved CSV batch", exact: true });
    await batchPanel.getByText(`1. ${fixture.material_name} — ${fixture.technical_identity}`, { exact: true }).click();
    const packaging = batchPanel.getByRole("article", { name: "Material packaging", exact: true });
    await packaging.locator("summary").click();
    const acknowledge = async (reason: string) => {
      await packaging.getByLabel("Reason for packaging action").fill(reason);
      await packaging.getByRole("checkbox", { name: "I reviewed the selected batch, ZIP rule and job progress.", exact: true }).check();
    };
    await expect(packaging.getByRole("button", { name: "Reserve packaging job", exact: true })).toBeVisible();
    await acknowledge("Create actual synthetic local packages");
    await packaging.getByRole("button", { name: "Reserve packaging job", exact: true }).click();
    await expect(packaging.getByText("Job reserved. Start packaging when ready.", { exact: true })).toBeVisible();
    const jobs = await (await page.request.get(path + "/packaging-executions")).json();
    expect(jobs.enabled).toBe(true); expect(jobs.items).toHaveLength(1); expect(jobs.items[0].status).toBe("RESERVED");
    const runPath = path + "/packaging-executions/" + jobs.items[0].id + "/run";
    let lostRun = false; const runRequests: unknown[] = [];
    await page.route("**" + runPath, async (route) => {
      runRequests.push(route.request().postDataJSON());
      if (!lostRun) {
        lostRun = true; const complete = await route.fetch({ timeout: 60_000 });
        expect(complete.status()).toBe(200); expect((await complete.json()).status).toBe("PACKAGED");
        await route.abort("failed");
      } else await route.continue();
    });
    await acknowledge("Run approved synthetic packaging");
    await packaging.getByRole("button", { name: "Start packaging", exact: true }).click();
    await expect(packaging.getByRole("alert")).toContainText("The outcome is unknown");
    await packaging.getByRole("button", { name: "Recover same packaging request", exact: true }).click();
    await expect(packaging.getByRole("heading", { name: "Packaged", exact: true })).toBeVisible();
    expect(runRequests).toHaveLength(2); expect(runRequests[0]).toEqual(runRequests[1]);
    await page.unroute("**" + runPath);
    const historyPath = path + "/packaging-executions/" + jobs.items[0].id + "/dispatches";
    const actions = await (await page.request.get(historyPath)).json();
    expect(actions.items).toHaveLength(1); expect(actions.items[0].action).toBe("EXECUTE");
    expect(actions.items[0].observation.outcome).toBe("READY");
    expect(actions.items[0].observation.proof_sha256).toMatch(/^[a-f0-9]{64}$/);
    await page.setViewportSize({ width: 1280, height: 900 });
    await packaging.screenshot({ path: test.info().outputPath("packaging-job-desktop.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await packaging.screenshot({ path: test.info().outputPath("packaging-job-mobile.png") });
    await page.setViewportSize({ width: 1280, height: 900 });
    // A second, explicitly unsent reservation can be closed without conversion.
    await acknowledge("Reserve a synthetic closure case");
    await packaging.getByRole("button", { name: "Reserve packaging job", exact: true }).click();
    await expect(packaging.getByText("Job reserved. Start packaging when ready.", { exact: true })).toBeVisible();
    await acknowledge("Close the unsent synthetic reservation");
    await packaging.getByRole("button", { name: "Close packaging job", exact: true }).click();
    await expect(packaging.getByRole("heading", { name: "Closed", exact: true })).toBeVisible();
    // Current content changes after preparation; the already saved artifact must not.
    const current = await (await page.request.get(path + "/content")).json();
    expect((await page.request.post(path + "/content", { headers, data: { idempotency_key: crypto.randomUUID(), expected_revision: current.revision,
      description: "Later unapproved content", credits: 13, tags: ["changed"], category_ids: current.categories.map((item: { id: string }) => item.id), collection_ids: [], reason: "Verify immutable export after edit" } })).status()).toBe(200);
  }
  const history = await (await page.request.get("/api/publication-batches")).json(); expect(history.items).toHaveLength(1);
  const packagingHistory = await (await page.request.get(path + "/packaging-executions")).json();
  expect(packagingHistory.items).toHaveLength(2);
  expect(packagingHistory.items.map((item: { status: string }) => item.status).sort()).toEqual(["PACKAGED", "REJECTED"]);
  expect(packagingHistory.items.find((item: { status: string }) => item.status === "PACKAGED").proof_sha256).toMatch(/^[a-f0-9]{64}$/);
  const summary = history.items[0];
  await page.getByRole("button", { name: "Load latest batches", exact: true }).click();
  await page.getByRole("button", { name: `Open batch ${summary.id}`, exact: true }).click();
  const saved = page.getByRole("group", { name: "Saved CSV batch", exact: true });
  await expect(saved).toContainText("This CSV is a historical snapshot");
  await saved.getByText(`1. ${fixture.material_name} — ${fixture.technical_identity}`, { exact: true }).click();
  await expect(saved).toContainText("Synthetic approved export"); await expect(saved).not.toContainText("Later unapproved content");
  const downloaded = page.waitForEvent("download"); await saved.getByRole("button", { name: "Download saved CSV", exact: true }).click();
  const download = await downloaded; expect(download.suggestedFilename()).toBe(`publication-${summary.id}.csv`);
  const csv = await readFile((await download.path())!);
  expect(createHash("sha256").update(csv).digest("hex")).toBe(summary.csv_sha256);
  expect(csv.subarray(0, 3).equals(Buffer.from([239, 187, 191]))).toBe(true);
  expect(csv.toString("utf8")).toContain(";12;12.5x34 cm;"); expect(csv.toString("utf8")).not.toContain("Later unapproved content");
  const currentPreview = await page.request.post("/api/publication-batches/preview", { headers, data: { material_ids: [fixture.id] } });
  expect(currentPreview.status()).toBe(200); expect((await currentPreview.json()).can_prepare).toBe(false);
  expect((await (await page.request.get(path)).json()).is_published).toBe(false);
  await saved.screenshot({ path: test.info().outputPath("publication-saved.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await saved.screenshot({ path: test.info().outputPath("publication-saved-mobile.png") });
  await page.goto("/materials/" + fixture.id);
  const policy = page.getByRole("article", { name: "ZIP packaging policy", exact: true });
  await policy.locator("summary").click();
  if (!retainedPass) {
    await policy.getByRole("button", { name: "Review ZIP policy change", exact: true }).click();
    await expect(policy.getByRole("region", { name: "ZIP policy change preview", exact: true })).toContainText("All current technical, publication and content approvals");
    await policy.getByLabel("Reason for ZIP policy decision").fill("Reviewed synthetic historical override");
    await policy.getByRole("checkbox", { name: "I reviewed this ZIP rule and its effect.", exact: true }).check();
    let discarded = false; const sent: unknown[] = [];
    await page.route("**" + path + "/packaging-policy/override", async (route) => {
      sent.push(route.request().postDataJSON());
      if (!discarded) { discarded = true; expect((await route.fetch()).status()).toBe(200); await route.abort("failed"); }
      else await route.continue();
    });
    await policy.getByRole("button", { name: "Confirm ZIP policy change", exact: true }).click();
    await expect(policy.getByRole("alert")).toContainText("The outcome is unknown");
    await expect(policy.getByLabel("Reason for ZIP policy decision")).toBeDisabled();
    await policy.getByRole("button", { name: "Retry same policy request", exact: true }).click();
    await expect(policy.getByText("ZIP policy saved.", { exact: true })).toBeVisible();
    expect(sent).toHaveLength(2); expect(sent[0]).toEqual(sent[1]);
    await page.unroute("**" + path + "/packaging-policy/override");
  }
  await expect(policy.getByText("Legacy · normalize ZIP dates to 1 January 2026", { exact: true })).toBeVisible();
  await policy.getByRole("button", { name: "Load policy history", exact: true }).click();
  await expect(policy.getByRole("listitem")).toHaveCount(2);
  await expect(policy.getByRole("listitem").last()).toContainText("Freeze original synthetic ZIP dates");
  const policyReview = await (await page.request.get(path + "/review")).json();
  expect(policyReview.revision_hash).toBe(null);
  expect((await page.request.get("/api/publication-batches/" + summary.id + "/csv")).status()).toBe(200);
  expect((await (await page.request.get("/api/publication-batches/" + summary.id)).json()).csv_sha256).toBe(summary.csv_sha256);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await policy.screenshot({ path: test.info().outputPath("packaging-policy-mobile.png") });
  await page.setViewportSize({ width: 1280, height: 900 });
  await policy.screenshot({ path: test.info().outputPath("packaging-policy-desktop.png") });
  // Capture the persisted job in both passes; Playwright clears first-pass output.
  const retainedJob = page.getByRole("article", { name: "Material packaging", exact: true });
  await retainedJob.locator("summary").click();
  await retainedJob.getByRole("button", { name: /^Open Packaged ·/ }).click();
  await expect(retainedJob.getByRole("heading", { name: "Packaged", exact: true })).toBeVisible();
  await retainedJob.getByRole("button", { name: "Load packaging actions", exact: true }).click();
  await expect(retainedJob.getByRole("listitem").last()).toContainText("execute · Run approved synthetic packaging · ready");
  await retainedJob.screenshot({ path: test.info().outputPath("packaging-job-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await retainedJob.screenshot({ path: test.info().outputPath("packaging-job-mobile.png") });
});
