import { expect, test, request } from "@playwright/test";
import { runManifest } from "./run-manifest";
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
  await expect(page.getByRole("article", { name: "AI proposals", exact: true })).toHaveCount(0);
  await expect(page.getByRole("article", { name: "Content approval", exact: true })).toHaveCount(0);
  const approve = async () => {
    const review = await (await page.request.get(api + "/content-review")).json();
    const saved = await page.request.post(api + "/content/approve", { headers, data: { idempotency_key: crypto.randomUUID(), expected_revision: review.content_revision,
      expected_context_hash: review.context_hash, warnings_acknowledged: true, note: "Synthetic retained compatibility approval" } });
    expect(saved.status()).toBe(200);
  };
  if (!retainedPass) {
    const source = await page.request.post(api + "/content-sources", { headers, data: { idempotency_key: crypto.randomUUID(),
      url: "https://catalog.example/synthetic-stone", reason: "Approve synthetic documentation source" } });
    expect(source.status()).toBe(201);
    await approve();
    const context = await (await page.request.get(api + "/publishing-context")).json();
    const proposal = await page.request.post(api + "/content-drafts", { headers, data: { idempotency_key: crypto.randomUUID(), expected_context_hash: context.context_hash,
      provider: "Synthetic test provider", model: "fixture-v1", prompt_version: "pbr-1", description: "Original AI proposal text", tags: ["stone", "rough"],
      source_link_ids: [(await source.json()).id], reason: "Record synthetic source-based proposal" } });
    expect(proposal.status()).toBe(201);
    expect((await (await page.request.get(api + "/content")).json()).content_status).toBe("APPROVED");
    const draft = await proposal.json();
    const adopted = await page.request.post(api + `/content-drafts/${draft.id}/adopt`, { headers, data: { idempotency_key: crypto.randomUUID(), expected_revision: context.content_revision,
      expected_context_hash: context.context_hash, description: "Human verified synthetic stone description", tags: ["stone", "matte"], reason: "Correct synthetic wording before publication" } });
    expect(adopted.status()).toBe(200);
    expect((await (await page.request.get(api + "/content-review")).json()).approval).toBeNull();
    await approve();
  }
  await page.reload();
  const content = page.getByRole("article", { name: "Publication content", exact: true });
  await expect(content.getByText(/Revision 2 · Saved content/)).toBeVisible();
  await expect(content).not.toContainText("human approval");
  await expect(content.getByRole("textbox", { name: "Description", exact: true })).toHaveValue("Human verified synthetic stone description");
  await expect(content.getByLabel("Credits", { exact: true })).toHaveValue("7");
  const proposals = await (await page.request.get(api + "/content-drafts")).json(); expect(proposals.items).toHaveLength(1);
  expect(proposals.items[0].context_is_current).toBe(false); expect(proposals.items[0].description).toBe("Original AI proposal text");
  const saved = await (await page.request.get(api + "/content")).json();
  expect(saved).toMatchObject({ revision: 2, content_status: "APPROVED", credits: 7, tags: ["matte", "stone"], collections: [],
    ai_provenance: { draft_id: proposals.items[0].id, edited: true, provider: "Synthetic test provider", model: "fixture-v1", prompt_version: "pbr-1" } });
  expect(saved.categories).toContainEqual(expect.objectContaining({ value: "E2E AI Stone" }));
  expect(saved.categories.find((item: { id: string }) => item.id === saved.required_category_id)).toMatchObject({ is_required: true, source: "MAIN_CATEGORY" });
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
      await page.goto(`/materials/${id}`);
      await expect(page.getByRole("article", { name: "AI service access", exact: true })).toHaveCount(0);
      const revoked = await page.request.post(target + `/ai-service-credentials/${credential.credential.id}/revoke`, { headers,
        data: { idempotency_key: crypto.randomUUID(), reason: "Synthetic service work complete" } });
      expect(revoked.status()).toBe(200);
      expect((await ai.get(`/api/ai/materials/${id}/publishing-context`)).status()).toBe(401);
      expect((await ai.post(`/api/ai/materials/${id}/content-drafts`, { data: proposal })).status()).toBe(401);
    } finally { await ai.dispose(); }
  } else {
    const rows = await (await page.request.get("/api/materials?search=E2E%20AI%20Service")).json();
    expect(rows).toHaveLength(1); id = rows[0].id;
  }
  const target = `/api/materials/${id}`;
  await page.goto(`/materials/${id}`);
  await expect(page.getByRole("article", { name: "AI proposals", exact: true })).toHaveCount(0);
  const content = await (await page.request.get(target + "/content")).json(); expect(content.revision).toBe(0);
  const grants = await (await page.request.get(target + "/ai-service-credentials")).json(); expect(grants.items).toHaveLength(1);
  expect(grants.items[0].revoked_at === null).toBe(false); expect(Object.keys(grants.items[0])).not.toContain("token_hash");
  await expect(page.getByRole("article", { name: "AI service access", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("One-time service credential", { exact: true })).toHaveCount(0);
  const drafts = await (await page.request.get(target + "/content-drafts")).json(); expect(drafts.items).toHaveLength(1);
  expect(drafts.items[0].service_credential_id).toBe(grants.items[0].id);
  expect(drafts.items[0].description).toBe("Synthetic service proposal awaiting human review");
});
