import type { CustomerAiBrief, CustomerAiResult, CustomerAiResults, CustomerAiReviewRow, CustomerAiReceipt } from "../api/customerAiBriefClient";
import { customers } from "./directoryFixtures";

export const aiCustomers = customers.slice(0, 3).map((row, index) => ({ ...row, description: null, website: null, updatedAt: `2026-10-06T10:00:0${index}.123456+00:00` }));
export const customerAiBatch = "81000000-0000-4000-8000-000000000001";
export function customerAiResult(id = aiCustomers[0].id): CustomerAiResult {
  return { customer_id: id, context_hash: "a".repeat(64), expected_updated_at: aiCustomers.find(row => row.id === id)?.updatedAt ?? aiCustomers[0].updatedAt,
    description: "A manufacturer of verified architectural surfaces.", website: "https://manufacturer.com/", source_urls: ["https://manufacturer.com/about"], needs_review: false, note: "Official company page." };
}
export function customerAiResults(ids = [aiCustomers[0].id]): CustomerAiResults { return { schema_version: "reawote-customer-ai-results-v1", batch_id: customerAiBatch, items: ids.map(customerAiResult) }; }
export function customerAiRow(id = aiCustomers[0].id): CustomerAiReviewRow {
  const result = customerAiResult(id);
  return { customer_id: id, name: aiCustomers.find(row => row.id === id)?.name ?? "Other customer", current_description: null, current_website: null,
    proposed_description: result.description, proposed_website: result.website, source_urls: result.source_urls, needs_review: false, note: result.note,
    status: "READY", applicable: true, requires_description_overwrite: false, requires_website_overwrite: false, result };
}
export function customerAiReceipt(id = aiCustomers[0].id): CustomerAiReceipt { const result = customerAiResult(id); return { customer_id: id, batch_id: customerAiBatch, status: "APPLIED", updated_at: "2026-10-06T11:00:00.654321+00:00", description: result.description, website: result.website }; }
export function customerAiBrief(): CustomerAiBrief { return {
  schema_version: "reawote-customer-ai-brief-v1", batch_id: customerAiBatch, generated_at: "2026-10-06T10:30:00Z", instructions: "Research the explicitly selected customers.", result_json_schema: { type: "object" },
  result_template: customerAiResults(), items: [{ customer_id: aiCustomers[0].id, context_hash: customerAiResult().context_hash, expected_updated_at: aiCustomers[0].updatedAt,
    context: { name: aiCustomers[0].name, current_description: null, current_website: null } }], skipped: [],
}; }
