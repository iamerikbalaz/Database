import { expect, test, request } from "@playwright/test";
import path from "node:path";
import { e2eOutputDirectory, runManifest } from "./run-manifest";
import { retainedPass, signInThroughApi } from "./auth-helpers";

test("approved sources and human-reviewed AI proposals retain provenance after restart", async ({ page }) => {
  await signInThroughApi(page);
  const session = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": session.csrf_token };
  let id: string;
  if (!retainedPass) {
    const original = await (await page.request.get(`/api/materials/${runManifest.state.valid.id}`)).json();
    const created = await page.request.post("/api/materials", { headers, data: {
      project_id: runManifest.state.projectId, published_brand_id: runManifest.state.brandId,
      assigned_processor_id: original.assigned_processor_id, material_name: "E2E AI Provenance", main_category_code: "G03",
    } });
    expect(created.status()).toBe(201); id = (await created.json()).id;
    const category = await page.request.post("/api/online-categories", { headers, data: {
      idempotency_key: crypto.randomUUID(), value: "E2E AI Stone",
    } });
    expect(category.status()).toBe(201);
    expect((await page.request.post(`/api/materials/${id}/content`, { headers, data: {
      idempotency_key: crypto.randomUUID(), expected_revision: 0, description: "Original synthetic publication text", credits: 7,
      tags: ["original"], category_ids: [(await category.json()).id], collection_ids: [], reason: "Prepare synthetic adoption comparison",
    } })).status()).toBe(200);
  } else {
    const materials = await (await page.request.get("/api/materials?search=E2E%20AI%20Provenance")).json();
    expect(materials).toHaveLength(1); id = materials[0].id;
  }
  const api = `/api/materials/${id}`;
  await page.goto(`/materials/${id}`);
  const panel = page.getByRole("article", { name: "AI proposals", exact: true });
  const content = page.getByRole("article", { name: "Publication content", exact: true });
  const approval = page.getByRole("article", { name: "Content approval", exact: true });
  await panel.getByRole("button", { name: "Load AI workspace", exact: true }).click();
  await expect(panel.getByRole("heading", { name: "AI proposal history" })).toBeVisible();
  if (!retainedPass) {
    await panel.getByText("Approved source URLs", { exact: true }).click();
    await panel.getByLabel("Reason for source approval or availability change").fill("Approve synthetic documentation source");
    await panel.getByLabel("Source URL", { exact: true }).fill("https://catalog.example/synthetic-stone");
    await panel.getByRole("button", { name: "Approve source URL", exact: true }).click();
    await expect(panel.getByRole("heading", { name: "AI proposal history" })).toBeVisible();
    await expect.poll(async () => (await (await page.request.get(api + "/content-sources")).json()).length).toBe(1);
    // Establish a real human approval before proposal intake. Saving a proposal
    // alone must preserve it; actual adoption must invalidate it.
    await approval.getByRole("button", { name: "Review saved content", exact: true }).click();
    await approval.getByRole("button", { name: "Confirm content approval", exact: true }).click();
    await expect(approval.getByText("Current content is approved.", { exact: true })).toBeVisible();
    await panel.getByRole("button", { name: "Load AI workspace", exact: true }).click();
    await panel.getByText("Record an AI proposal", { exact: true }).click();
    await panel.getByLabel("AI provider", { exact: true }).fill("Synthetic test provider");
    await panel.getByLabel("AI model", { exact: true }).fill("fixture-v1");
    await panel.getByLabel("Prompt version", { exact: true }).fill("pbr-1");
    await panel.getByLabel("Proposed description", { exact: true }).fill("Original AI proposal text");
    await panel.getByLabel("Proposed tags, one per line", { exact: true }).fill("stone\nrough");
    await panel.getByRole("checkbox", { name: "https://catalog.example/synthetic-stone", exact: true }).check();
    await panel.getByLabel("Reason for recording proposal", { exact: true }).fill("Record synthetic source-based proposal");
    await panel.getByRole("button", { name: "Save AI proposal", exact: true }).click();
    await expect(panel.getByRole("button", { name: "Compare and review proposal", exact: true })).toBeEnabled();
    expect((await (await page.request.get(api + "/content")).json()).content_status).toBe("APPROVED");
    await panel.getByRole("button", { name: "Compare and review proposal", exact: true }).click();
    const comparison = panel.getByRole("region", { name: "Review AI proposal", exact: true });
    await expect(comparison.getByRole("region", { name: "Currently saved content" })).toContainText("Original synthetic publication text");
    await comparison.getByRole("textbox", { name: "Reviewed description", exact: true }).fill("Human verified synthetic stone description");
    await comparison.getByRole("textbox", { name: "Reviewed tags, one per line", exact: true }).fill("stone\nmatte");
    await comparison.getByLabel("Reason for adopting proposal", { exact: true }).fill("Correct synthetic wording before publication");
    await expect(comparison.getByRole("button", { name: "Adopt reviewed content" })).toBeDisabled();
    await comparison.getByRole("checkbox", { name: /I reviewed the text/ }).check();
    const images = path.join(path.dirname(e2eOutputDirectory), "ai-first-pass");
    await comparison.screenshot({ path: path.join(images, "ai-comparison.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await comparison.screenshot({ path: path.join(images, "ai-comparison-mobile.png") });
    await page.setViewportSize({ width: 1280, height: 720 });
    await comparison.getByRole("button", { name: "Adopt reviewed content", exact: true }).click();
    await expect(content.getByRole("textbox", { name: "Description", exact: true })).toHaveValue("Human verified synthetic stone description");
    await expect(approval.getByText("Current content requires approval.", { exact: true })).toBeVisible();
    await expect(content).toContainText("Human content approval is still required.");
    await approval.getByRole("button", { name: "Review saved content", exact: true }).click();
    await approval.getByRole("button", { name: "Confirm content approval", exact: true }).click();
    await expect(approval.getByText("Current content is approved.", { exact: true })).toBeVisible();
    await panel.getByRole("button", { name: "Load AI workspace", exact: true }).click();
  }
  await expect(content).toContainText("This content has a current human approval.");
  await expect(content.getByRole("textbox", { name: "Description", exact: true })).toHaveValue("Human verified synthetic stone description");
  await expect(content.getByLabel("Credits", { exact: true })).toHaveValue("7");
  await expect(panel.getByRole("button", { name: "Compare and review proposal", exact: true })).toBeDisabled();
  await panel.getByText("Original proposal", { exact: true }).click();
  await expect(panel.getByText("Original AI proposal text", { exact: true })).toBeVisible();
  const proposals = await (await page.request.get(api + "/content-drafts")).json(); expect(proposals.items).toHaveLength(1);
  expect(proposals.items[0].context_is_current).toBe(false); expect(proposals.items[0].description).toBe("Original AI proposal text");
  const saved = await (await page.request.get(api + "/content")).json();
  expect(saved).toMatchObject({ revision: 2, content_status: "APPROVED", credits: 7, tags: ["matte", "stone"], categories: [{ value: "E2E AI Stone" }], collections: [],
    ai_provenance: { draft_id: proposals.items[0].id, edited: true, provider: "Synthetic test provider", model: "fixture-v1", prompt_version: "pbr-1" } });
  const history = await (await page.request.get(api + "/content-history")).json(); expect(history).toHaveLength(2);
  expect(history[0].snapshot.ai_provenance).toEqual(saved.ai_provenance);
  const decisions = await (await page.request.get(api + "/content-approvals")).json(); expect(decisions).toHaveLength(2);
  const sources = await (await page.request.get(api + "/content-sources")).json(); expect(sources).toHaveLength(1); expect(sources[0].is_active).toBe(true);
});

test("one-material AI service access is revocable and its proposal history survives restart", async ({ page }) => {
  await signInThroughApi(page);
  const auth = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": auth.csrf_token };
  let id: string;
  if (!retainedPass) {
    const original = await (await page.request.get(`/api/materials/${runManifest.state.valid.id}`)).json();
    const created = await page.request.post("/api/materials", { headers, data: {
      project_id: runManifest.state.projectId, published_brand_id: runManifest.state.brandId,
      assigned_processor_id: original.assigned_processor_id, material_name: "E2E AI Service", main_category_code: "G03",
    } });
    expect(created.status()).toBe(201); id = (await created.json()).id;
    const target = `/api/materials/${id}`;
    const issue = { idempotency_key: crypto.randomUUID(), reason: "Synthetic service scope verification", lifetime_seconds: 900 };
    const issued = await page.request.post(target + "/ai-service-credentials", { headers, data: issue });
    expect(issued.status()).toBe(201); const credential = await issued.json();
    // Keep the secret only in this request context's memory. Traces are disabled,
    // and no screenshots, logs or retained fixtures contain the secret.
    const ai = await request.newContext({ baseURL: runManifest.frontendUrl, extraHTTPHeaders: { Authorization: "Bearer " + credential.token } });
    try {
      const replay = await page.request.post(target + "/ai-service-credentials", { headers, data: issue });
      expect(replay.status()).toBe(201); expect((await replay.json()).token === null).toBe(true);
      const context = await ai.get(`/api/ai/materials/${id}/publishing-context`); expect(context.status()).toBe(200);
      const minimal = await context.json();
      expect(Object.keys(minimal.context).sort()).toEqual(["brand", "categories", "collections", "material_id", "name", "source_urls"]);
      expect((await ai.get(`/api/materials/${id}`)).status()).toBe(401);
      expect((await ai.get(`/api/ai/materials/${runManifest.state.valid.id}/publishing-context`)).status()).toBe(401);
      const proposal = { idempotency_key: crypto.randomUUID(), expected_context_hash: minimal.context_hash, provider: "Synthetic scoped service",
        model: "fixture-service-v1", prompt_version: "pbr-1", description: "Synthetic service proposal awaiting human review", tags: ["stone"],
        source_link_ids: [], reason: "Record a synthetic proposal through restricted service access" };
      const posted = await ai.post(`/api/ai/materials/${id}/content-drafts`, { data: proposal });
      expect(posted.status()).toBe(201); expect(Object.keys(await posted.json()).sort()).toEqual(["context_hash", "id", "status"]);
      expect((await page.request.post(target + "/ai-service-credentials/" + credential.credential.id + "/revoke", {
        headers, data: { idempotency_key: crypto.randomUUID(), reason: "Synthetic service work complete" },
      })).status()).toBe(200);
      expect((await ai.get(`/api/ai/materials/${id}/publishing-context`)).status()).toBe(401);
      expect((await ai.post(`/api/ai/materials/${id}/content-drafts`, { data: proposal })).status()).toBe(401);
    } finally { await ai.dispose(); }
  } else {
    const rows = await (await page.request.get("/api/materials?search=E2E%20AI%20Service")).json();
    expect(rows).toHaveLength(1); id = rows[0].id;
  }
  const target = `/api/materials/${id}`;
  await page.goto(`/materials/${id}`);
  const panel = page.getByRole("article", { name: "AI proposals", exact: true });
  await panel.getByRole("button", { name: "Load AI workspace", exact: true }).click();
  await panel.getByText("Original proposal", { exact: true }).click();
  await expect(panel.getByText("Synthetic service proposal awaiting human review", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Compare and review proposal", exact: true })).toBeEnabled();
  const content = await (await page.request.get(target + "/content")).json(); expect(content.revision).toBe(0);
  const grants = await (await page.request.get(target + "/ai-service-credentials")).json(); expect(grants.items).toHaveLength(1);
  expect(grants.items[0].revoked_at === null).toBe(false); expect(Object.keys(grants.items[0])).not.toContain("token_hash");
  const drafts = await (await page.request.get(target + "/content-drafts")).json(); expect(drafts.items).toHaveLength(1);
  expect(drafts.items[0].service_credential_id).toBe(grants.items[0].id);
});
