import { afterEach, expect, it, vi } from "vitest";
import { aiContentClient, aiDraftFromDto, publishingContextFromDto, sourceFromDto } from "./aiContentClient";
import { materialDto, processorDto } from "../test/materialFixtures";

const id = materialDto.id, sourceId = "10000000-0000-4000-8000-000000000001", draftId = "20000000-0000-4000-8000-000000000001";
const context = () => ({ material_id: id, name: "Synthetic surface", brand: { id: materialDto.published_brand_id, name: "Brand" },
  categories: [{ id: sourceId, value: "Stone" }], collections: [], source_urls: [{ id: sourceId, url: "https://catalog.example/stone" }] });
const draft = () => ({ id: draftId, material_id: id, actor_id: processorDto.id, status: "AI_DRAFT", context_hash: "a".repeat(64), content_revision: 1,
  context: context(), provider: "Synthetic tool", model: "fixture-v1", prompt_version: "pbr-1", description: "Synthetic description", tags: ["stone"],
  source_link_ids: [sourceId], reason: "Human supplied proposal", created_at: "2026-09-17T12:00:00Z", context_is_current: true });
afterEach(() => vi.unstubAllGlobals());
it("keeps only minimal context and binds its material and revision", () => {
  const parsed = publishingContextFromDto({ context: context(), context_hash: "a".repeat(64), content_revision: 1 }, id);
  expect(parsed.context.sourceUrls[0].url).toBe("https://catalog.example/stone"); expect(parsed.contentRevision).toBe(1);
  expect(() => publishingContextFromDto({ context: context(), context_hash: "a".repeat(64), content_revision: 1 }, draftId)).toThrow();
});
it.each(["identity", "context", "hash", "status", "sources", "tags", "date", "duplicate"])("rejects inconsistent AI proposal %s", (defect) => {
  const value = draft();
  if (defect === "identity") value.material_id = sourceId;
  if (defect === "context") value.context.material_id = sourceId;
  if (defect === "hash") value.context_hash = "bad";
  if (defect === "status") value.status = "APPROVED";
  if (defect === "sources") value.source_link_ids = [draftId];
  if (defect === "tags") value.tags = ["stone:wood"];
  if (defect === "date") value.created_at = "invalid";
  if (defect === "duplicate") value.source_link_ids.push(sourceId);
  expect(() => aiDraftFromDto(value, id)).toThrow();
});
it.each(["javascript:alert(1)", "http://catalog.example", "https://user:secret@catalog.example/x", "https://catalog.example/x?secret=value"])("rejects unsafe source links", (url) => {
  expect(() => sourceFromDto({ id: sourceId, url, version: 1, is_active: true })).toThrow();
});
it("uses authenticated source and proposal writes with the caller's exact key", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ id: sourceId, url: "https://catalog.example/stone", version: 1, is_active: true })))
    .mockResolvedValueOnce(new Response(JSON.stringify(draft())));
  vi.stubGlobal("fetch", fetcher);
  const approval = { idempotency_key: draftId, url: "https://catalog.example/stone", reason: "Approved source" };
  await aiContentClient.approveSource(id, approval);
  expect(fetcher).toHaveBeenNthCalledWith(1, expect.stringContaining(`/materials/${id}/content-sources`), expect.objectContaining({ method: "POST", credentials: "same-origin", cache: "no-store", body: JSON.stringify(approval) }));
  const proposal = { idempotency_key: sourceId, expected_context_hash: "a".repeat(64), provider: "Synthetic tool", model: "fixture-v1", prompt_version: "pbr-1", description: "Synthetic description", tags: ["stone"], source_link_ids: [sourceId], reason: "Human supplied proposal" };
  expect((await aiContentClient.submit(id, proposal)).id).toBe(draftId);
  expect(fetcher).toHaveBeenNthCalledWith(2, expect.stringContaining("/content-drafts"), expect.objectContaining({ method: "POST", body: JSON.stringify(proposal) }));
});
it("rejects adoption results without the expected material and immutable proposal reference", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ material_id: id, revision: 2, description: null, credits: 10,
    tags: [], categories: [], collections: [], content_status: "MANUAL_DRAFT" }))));
  await expect(aiContentClient.adopt(id, draftId, { idempotency_key: sourceId, expected_revision: 1, expected_context_hash: "a".repeat(64), description: null, tags: [], reason: "Adopt proposal" })).rejects.toThrow();
});
it("requires a current-context flag on every paged history row", async () => {
  const value: Partial<ReturnType<typeof draft>> = draft(); delete value.context_is_current;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [value], next_cursor: null }))));
  await expect(aiContentClient.drafts(id)).rejects.toThrow();
});
