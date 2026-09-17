import { expect, test } from "@playwright/test";
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
