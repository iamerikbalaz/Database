import { afterEach, expect, it, vi } from "vitest";
import { AI_BRIEF_MAX_BYTES, aiBriefClient, parseAiBriefResults, parseAiBriefResultsText } from "./aiBriefClient";
import { materialDto } from "../test/materialFixtures";

const id = materialDto.id, batch = "10000000-0000-4000-8000-000000000001", second = "50000000-0000-4000-8000-000000000002";
const item = () => ({ material_id: id, context_hash: "a".repeat(64), content_revision: 2, description: "A textured material.", source_urls: ["https://manufacturer.com/product"], needs_review: false, note: "Product page." });
const results = () => ({ schema_version: "reawote-ai-results-v1" as const, batch_id: batch, items: [item()] });
const row = () => ({ material_id: id, name: "Material", current_description: null, proposed_description: item().description, source_urls: item().source_urls, needs_review: false, note: item().note, status: "READY", applicable: true, requires_overwrite: false, result: item() });
const brief = () => ({ schema_version: "reawote-ai-brief-v1", batch_id: batch, generated_at: materialDto.updated_at, instructions: "Research the selected materials.", result_json_schema: { type: "object" }, result_template: results(), items: [{ material_id: id, context_hash: item().context_hash, content_revision: 2, context: { name: "Material", customer: { id: batch, name: "Customer", website: "https://manufacturer.com/" }, categories: [], collections: [], source_urls: [], current_description: null } }], skipped: [] });
function respond(value: unknown) { const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(value))); vi.stubGlobal("fetch", fetcher); return fetcher; }
afterEach(() => vi.unstubAllGlobals());

it("exports only the frozen IDs and revisions and validates the complete brief/template mapping", async () => {
  const fetcher = respond(brief()), selection = [{ id, expected_updated_at: materialDto.updated_at }];
  expect((await aiBriefClient.generate(selection)).items).toHaveLength(1);
  expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("/material-ai/brief"), expect.objectContaining({ method: "POST", body: JSON.stringify({ selection }), credentials: "same-origin" }));
  const invalid = brief(); invalid.result_template.items[0].context_hash = "b".repeat(64); respond(invalid);
  await expect(aiBriefClient.generate(selection)).rejects.toThrow("different context");
});

it("normalizes imported description, note and source URLs consistently with the server", async () => {
  const imported = results(); imported.items[0] = { ...item(), description: "  Cafe\u0301\r\nTexture.  ", note: "  Verified. ", source_urls: ["https://MANUFACTURER.com:443"] };
  const parsed = parseAiBriefResultsText(JSON.stringify(imported));
  expect(parsed.items[0]).toMatchObject({ description: "Café\nTexture.", note: "Verified.", source_urls: ["https://manufacturer.com/"] });
  const fetcher = respond({ batch_id: batch, items: [{ ...row(), proposed_description: parsed.items[0].description, source_urls: parsed.items[0].source_urls, note: parsed.items[0].note, result: parsed.items[0] }] });
  expect((await aiBriefClient.review(parsed, [id])).items[0].applicable).toBe(true);
  expect(fetcher).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ body: JSON.stringify({ results: parsed, selected_ids: [id] }) }));
});

it.each(["http://manufacturer.com/a", "https://127.0.0.1/a", "https://8.8.8.8/a", "https://[::1]/a", "https://internal.local/a", "https://user:secret@manufacturer.com/a", "https://@manufacturer.com/a", "https://manufacturer.com/a?q=secret", "https://manufacturer.com/a#section", "https://manufacturer.com/a%20b", "https://manufacturer.com:444/a", "javascript:alert(1)"])("rejects unsafe or unsupported source URL %s", url => {
  const data = results(); data.items[0].source_urls = [url];
  expect(() => parseAiBriefResults(data)).toThrow(/HTTPS/);
});

it("rejects extra fields, duplicate items and sources, oversized JSON and invalid JSON", () => {
  expect(() => parseAiBriefResults({ ...results(), prompt: "unexpected" })).toThrow("unexpected");
  expect(() => parseAiBriefResults({ ...results(), items: [{ ...item(), tags: [] }] })).toThrow("unexpected");
  expect(() => parseAiBriefResults({ ...results(), items: [item(), item()] })).toThrow("duplicate");
  expect(() => parseAiBriefResults({ ...results(), items: [{ ...item(), source_urls: ["https://manufacturer.com", "https://MANUFACTURER.com/"] }] })).toThrow("duplicate");
  expect(() => parseAiBriefResultsText(" ".repeat(AI_BRIEF_MAX_BYTES + 1))).toThrow("5 MiB");
  expect(() => parseAiBriefResultsText("not JSON")).toThrow("valid JSON");
  expect(() => parseAiBriefResults({ ...results(), items: [{ ...item(), description: "bad\u0000text" }] })).toThrow("control");
});

it("rejects out-of-selection results before sending a request and accepts missing selected results", async () => {
  const fetcher = respond({ batch_id: batch, items: [row(), { ...row(), material_id: second, name: "Missing", proposed_description: null, source_urls: [], needs_review: true, note: "", status: "MISSING_RESULT", applicable: false, result: null }] });
  await expect(aiBriefClient.review(results(), [second])).rejects.toThrow("outside");
  expect(fetcher).not.toHaveBeenCalled();
  expect((await aiBriefClient.review(results(), [id, second])).items[1].status).toBe("MISSING_RESULT");
});

it.each(["batch", "material", "description", "applicable", "overwrite"])("rejects inconsistent review response: %s", defect => {
  const value = { batch_id: batch, items: [row()] };
  if (defect === "batch") value.batch_id = second;
  if (defect === "material") value.items[0].material_id = second;
  if (defect === "description") value.items[0].proposed_description = "Unexpected replacement";
  if (defect === "applicable") value.items[0].applicable = false;
  if (defect === "overwrite") value.items[0].requires_overwrite = true;
  respond(value); return expect(aiBriefClient.review(results(), [id])).rejects.toThrow();
});

it("sends exact per-material packets and verifies save receipts", async () => {
  const receipt = { material_id: id, batch_id: batch, draft_id: second, status: "APPLIED", content_revision: 3, description: item().description };
  const fetcher = respond(receipt), payload = { idempotency_key: second, batch_id: batch, result: item(), overwrite: false };
  expect(await aiBriefClient.apply(id, payload)).toEqual(receipt);
  expect(fetcher).toHaveBeenCalledWith(expect.stringContaining(`/materials/${id}/ai-brief-result`), expect.objectContaining({ body: JSON.stringify(payload) }));
  respond({ ...receipt, content_revision: 4 });
  await expect(aiBriefClient.apply(id, payload)).rejects.toThrow("verified");
  respond({ ...receipt, material_id: second });
  await expect(aiBriefClient.apply(id, payload)).rejects.toThrow("verified");
});
