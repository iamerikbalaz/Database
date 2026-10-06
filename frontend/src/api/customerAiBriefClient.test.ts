import { afterEach, expect, it, vi } from "vitest";
import { CUSTOMER_AI_MAX_BYTES, customerAiBriefClient, parseCustomerAiResults, parseCustomerAiResultsText } from "./customerAiBriefClient";
import { aiCustomers, customerAiBatch, customerAiBrief, customerAiReceipt, customerAiResult, customerAiResults, customerAiRow } from "../test/customerAiFixtures";

const first = aiCustomers[0], second = aiCustomers[1];
function respond(value: unknown) { const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(value))); vi.stubGlobal("fetch", fetcher); return fetcher; }
afterEach(() => vi.unstubAllGlobals());

it("exports only frozen IDs and exact microsecond timestamps, and binds the template to its context", async () => {
  const fetcher = respond(customerAiBrief()), selection = [{ id: first.id, expected_updated_at: first.updatedAt }];
  const brief = await customerAiBriefClient.generate(selection);
  expect(brief.items[0].expected_updated_at).toBe(first.updatedAt);
  expect(fetcher).toHaveBeenCalledWith("/api/customer-ai/brief", expect.objectContaining({ method: "POST", body: JSON.stringify({ selection }), credentials: "same-origin", cache: "no-store" }));
  const invalid = customerAiBrief(); invalid.result_template.items[0].context_hash = "b".repeat(64); respond(invalid);
  await expect(customerAiBriefClient.generate(selection)).rejects.toThrow("different context");
});

it("keeps revisions exact while normalizing plain text, null proposals and public URLs", () => {
  const data = customerAiResults(); data.items[0] = { ...customerAiResult(), description: "  Cafe\u0301\r\nSurfaces.  ", website: "  https://MANUFACTURER.com:443  ", note: "  Verified. " };
  expect(parseCustomerAiResultsText(JSON.stringify(data)).items[0]).toMatchObject({ expected_updated_at: first.updatedAt, description: "Café\nSurfaces.", website: "https://manufacturer.com/", note: "Verified." });
  data.items[0].description = " "; data.items[0].website = " ";
  expect(parseCustomerAiResults(data).items[0]).toMatchObject({ description: null, website: null });
});

it.each(["http://manufacturer.com", "https://127.0.0.1", "https://[::1]", "https://internal.local", "https://name:secret@manufacturer.com", "https://manufacturer.com?a=secret", "https://manufacturer.com/#part", "https://manufacturer.com:444", "javascript:alert(1)"])("rejects unsafe proposed websites and citations %s", url => {
  const data = customerAiResults(); data.items[0].website = url;
  expect(() => parseCustomerAiResults(data)).toThrow(/HTTPS/);
  data.items[0].website = null; data.items[0].source_urls = [url]; expect(() => parseCustomerAiResults(data)).toThrow(/HTTPS/);
});

it("rejects Material files, duplicate IDs/citations, unexpected properties and bounded input violations", () => {
  expect(() => parseCustomerAiResults({ ...customerAiResults(), schema_version: "reawote-ai-results-v2" })).toThrow("Material");
  expect(() => parseCustomerAiResults({ ...customerAiResults(), notes: "Private" })).toThrow("unexpected");
  expect(() => parseCustomerAiResults({ ...customerAiResults(), items: [customerAiResult(), customerAiResult()] })).toThrow("duplicate");
  expect(() => parseCustomerAiResults({ ...customerAiResults(), items: [{ ...customerAiResult(), source_urls: ["https://manufacturer.com", "https://MANUFACTURER.com/"] }] })).toThrow("duplicate");
  expect(() => parseCustomerAiResults({ ...customerAiResults(), items: [{ ...customerAiResult(), description: "x".repeat(20001) }] })).toThrow("long");
  expect(() => parseCustomerAiResults({ ...customerAiResults(), items: [{ ...customerAiResult(), description: "bad\u0000text" }] })).toThrow("control");
  expect(() => parseCustomerAiResultsText(" ".repeat(CUSTOMER_AI_MAX_BYTES + 1))).toThrow("5 MiB");
  expect(() => parseCustomerAiResultsText("invalid")).toThrow("valid JSON");
  expect(() => parseCustomerAiResultsText(JSON.stringify({ ...customerAiResults(), items: [] }))).toThrow("no customers");
});

it("reviews standalone customer IDs and retains already-applied rows without applying them", async () => {
  const fetcher = respond({ batch_id: customerAiBatch, items: [{ ...customerAiRow(), status: "ALREADY_APPLIED", applicable: false, current_description: "Later manual edit" }] });
  expect((await customerAiBriefClient.review(customerAiResults())).items[0].status).toBe("ALREADY_APPLIED");
  expect(fetcher).toHaveBeenCalledWith("/api/customer-ai/review", expect.objectContaining({ body: JSON.stringify({ results: customerAiResults(), selected_ids: [first.id] }) }));
});

it("rejects out-of-selection results before sending and permits missing selected results", async () => {
  const fetcher = respond({ batch_id: customerAiBatch, items: [customerAiRow(), { ...customerAiRow(second.id), result: null, proposed_description: null, proposed_website: null, status: "MISSING_RESULT", applicable: false }] });
  await expect(customerAiBriefClient.review(customerAiResults(), [second.id])).rejects.toThrow("outside");
  expect(fetcher).not.toHaveBeenCalled();
  expect((await customerAiBriefClient.review(customerAiResults(), [first.id, second.id])).items[1].status).toBe("MISSING_RESULT");
});

it.each(["batch", "id", "description", "website", "applicable", "overwrite_description", "overwrite_website"])("rejects inconsistent review responses: %s", defect => {
  const value = { batch_id: customerAiBatch, items: [customerAiRow()] };
  if (defect === "batch") value.batch_id = second.id;
  if (defect === "id") value.items[0].customer_id = second.id;
  if (defect === "description") value.items[0].proposed_description = "Unexpected";
  if (defect === "website") value.items[0].proposed_website = "https://unexpected.com/";
  if (defect === "applicable") value.items[0].applicable = false;
  if (defect === "overwrite_description") value.items[0].requires_description_overwrite = true;
  if (defect === "overwrite_website") value.items[0].requires_website_overwrite = true;
  respond(value); return expect(customerAiBriefClient.review(customerAiResults())).rejects.toThrow();
});

it("preserves a null field and verifies exact saved proposal fields and receipt IDs", async () => {
  const result = { ...customerAiResult(), description: null }, payload = { idempotency_key: second.id, batch_id: customerAiBatch, result, overwrite_description: false, overwrite_website: true };
  const receipt = { ...customerAiReceipt(), description: "Preserved description" }, fetcher = respond(receipt);
  expect(await customerAiBriefClient.apply(first.id, payload)).toEqual(receipt);
  expect(fetcher).toHaveBeenCalledWith(`/api/customers/${first.id}/ai-brief-result`, expect.objectContaining({ body: JSON.stringify(payload) }));
  respond({ ...receipt, website: "https://unexpected.com/" }); await expect(customerAiBriefClient.apply(first.id, payload)).rejects.toThrow("verified");
  respond({ ...receipt, customer_id: second.id }); await expect(customerAiBriefClient.apply(first.id, payload)).rejects.toThrow("verified");
});
