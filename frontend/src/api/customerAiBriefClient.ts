import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";

export const CUSTOMER_AI_MAX_BYTES = 5 * 1024 * 1024;
export type CustomerAiSelection = { id: string; expected_updated_at: string }[];
export type CustomerAiResult = { customer_id: string; context_hash: string; expected_updated_at: string; description: string | null; website: string | null; source_urls: string[]; needs_review: boolean; note: string };
export type CustomerAiResults = { schema_version: "reawote-customer-ai-results-v1"; batch_id: string; items: CustomerAiResult[] };
export type CustomerAiBrief = { schema_version: "reawote-customer-ai-brief-v1"; batch_id: string; generated_at: string; instructions: string; result_json_schema: Record<string, unknown>; result_template: CustomerAiResults;
  items: { customer_id: string; context_hash: string; expected_updated_at: string; context: { name: string; current_description: string | null; current_website: string | null } }[];
  skipped: { customer_id: string; code: string; message: string }[] };
const statuses = ["READY", "OVERWRITE_REQUIRED", "ALREADY_APPLIED", "STALE_CONTEXT", "NEEDS_REVIEW", "EMPTY_PROPOSAL", "MISSING_RESULT", "CUSTOMER_UNAVAILABLE", "CUSTOMER_INACTIVE", "CUSTOMER_BUSY", "NO_CHANGES"] as const;
export type CustomerAiReviewRow = { customer_id: string; name: string; current_description: string | null; current_website: string | null; proposed_description: string | null; proposed_website: string | null;
  source_urls: string[]; needs_review: boolean; note: string; status: typeof statuses[number]; applicable: boolean; requires_description_overwrite: boolean; requires_website_overwrite: boolean; result: CustomerAiResult | null };
export type CustomerAiReview = { batch_id: string; items: CustomerAiReviewRow[] };
export type CustomerAiApply = { idempotency_key: string; batch_id: string; result: CustomerAiResult; overwrite_description: boolean; overwrite_website: boolean };
export type CustomerAiReceipt = { customer_id: string; batch_id: string; status: "APPLIED"; updated_at: string; description: string | null; website: string | null };

function bounded(value: unknown, max: number) { const text = string(value); if (text.length > max) throw new Error("Customer AI text is too long."); return text; }
function nullableText(value: unknown, max: number) { return value === null ? null : bounded(value, max); }
function list<T>(value: unknown, parse: (value: unknown) => T, max = 100): T[] { if (!Array.isArray(value) || value.length > max) throw new Error("Invalid Customer AI list."); return value.map(parse); }
function unique(ids: string[]) { if (ids.length !== new Set(ids).size) throw new Error("The Customer AI file contains duplicate customers."); }
function exactKeys(value: Record<string, unknown>, keys: string[]) { if (Object.keys(value).length !== keys.length || Object.keys(value).some(key => !keys.includes(key))) throw new Error("The Customer AI results contain missing or unexpected fields."); }
function hash(value: unknown) { const text = string(value); if (!/^[0-9a-f]{64}$/.test(text)) throw new Error("Invalid Customer AI context."); return text; }
function date(value: unknown) { const text = bounded(value, 64); if (!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(text) || !Number.isFinite(Date.parse(text))) throw new Error("Invalid Customer AI revision date."); return text; }
function plainText(value: unknown, max: number) { const text = bounded(value, max).trim(); if ([...text].some(char => /\p{C}/u.test(char) && !"\n\r\t".includes(char))) throw new Error("Customer AI results contain unsupported control characters."); return text.normalize("NFC").replace(/\r\n?/g, "\n"); }
function publicUrl(value: unknown) {
  const raw = bounded(value, 2048);
  try {
    const url = new URL(raw), host = url.hostname.toLowerCase();
    if (!raw.startsWith("https://") || url.protocol !== "https:" || url.username || url.password || raw.split("/")[2]?.includes("@") || url.port || /[?#]/.test(raw) || /[\p{C}\s\\]/u.test(decodeURIComponent(raw)) ||
      host.length > 253 || !host.includes(".") || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".internal") ||
      !/^[a-z][a-z0-9-]{1,62}$/.test(host.split(".").at(-1)!) || host.split(".").some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))) throw new Error();
    const path = raw.match(/^https:\/\/[^/]+(\/.*)?$/)?.[1] || "/";
    const normalized = `https://${host}${[...path].map(char => /[A-Za-z0-9/%:@!$&'()*+,;=\-._~]/.test(char) ? char : encodeURIComponent(char)).join("")}`;
    if (normalized.length > 2048) throw new Error();
    return normalized;
  } catch { throw new Error("Customer AI websites and sources must use public HTTPS addresses without credentials, query strings or fragments."); }
}
function resultItem(value: unknown): CustomerAiResult {
  const item = record(value); exactKeys(item, ["customer_id", "context_hash", "expected_updated_at", "description", "website", "source_urls", "needs_review", "note"]);
  const sources = list(item.source_urls, publicUrl, 20);
  if (new Set(sources).size !== sources.length) throw new Error("Customer AI results contain duplicate source URLs.");
  return { customer_id: uuid(item.customer_id), context_hash: hash(item.context_hash), expected_updated_at: date(item.expected_updated_at),
    description: item.description === null ? null : plainText(item.description, 20000) || null,
    website: item.website === null || (typeof item.website === "string" && !item.website.trim()) ? null : publicUrl(bounded(item.website, 2048).trim()), source_urls: sources, needs_review: boolean(item.needs_review), note: plainText(item.note, 2000) };
}
export function parseCustomerAiResults(value: unknown): CustomerAiResults {
  const item = record(value); exactKeys(item, ["schema_version", "batch_id", "items"]);
  if (item.schema_version !== "reawote-customer-ai-results-v1") throw new Error("This is not a Customer AI results JSON file. Material results use the separate Material import.");
  const items = list(item.items, resultItem); unique(items.map(row => row.customer_id));
  return { schema_version: item.schema_version, batch_id: uuid(item.batch_id), items };
}
export function parseCustomerAiResultsText(value: string) {
  if (new TextEncoder().encode(value).byteLength > CUSTOMER_AI_MAX_BYTES) throw new Error("Choose a JSON file no larger than 5 MiB.");
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { throw new Error("The selected file is not valid JSON."); }
  const result = parseCustomerAiResults(parsed);
  if (!result.items.length) throw new Error("The Customer AI results file has no customers.");
  return result;
}
function selectedIds(values: string[]) { const ids = list(values, uuid); unique(ids); if (!ids.length) throw new Error("Select between 1 and 100 distinct customers."); return ids; }
function matchingIds(actual: string[], expected: string[]) { unique(actual); if (actual.length !== expected.length || actual.some(id => !expected.includes(id))) throw new Error("The Customer AI response does not match the selected customers."); }
function parseBrief(value: unknown, ids: string[]): CustomerAiBrief {
  const item = record(value);
  if (item.schema_version !== "reawote-customer-ai-brief-v1") throw new Error("Invalid Customer AI brief format.");
  const batchId = uuid(item.batch_id), template = parseCustomerAiResults(item.result_template);
  if (template.batch_id !== batchId) throw new Error("The Customer AI template belongs to a different batch.");
  const items = list(item.items, value => { const row = record(value), context = record(row.context); return {
    customer_id: uuid(row.customer_id), context_hash: hash(row.context_hash), expected_updated_at: date(row.expected_updated_at),
    context: { name: bounded(context.name, 255), current_description: nullableText(context.current_description, 20000), current_website: nullableText(context.current_website, 2048) },
  }; });
  const skipped = list(item.skipped, value => { const row = record(value); return { customer_id: uuid(row.customer_id), code: bounded(row.code, 100), message: bounded(row.message, 2000) }; });
  matchingIds([...items.map(row => row.customer_id), ...skipped.map(row => row.customer_id)], ids);
  matchingIds(template.items.map(row => row.customer_id), items.map(row => row.customer_id));
  if (template.items.some(row => { const source = items.find(item => item.customer_id === row.customer_id)!; return row.context_hash !== source.context_hash || row.expected_updated_at !== source.expected_updated_at; })) throw new Error("The Customer AI template has a different context.");
  return { schema_version: item.schema_version, batch_id: batchId, generated_at: date(item.generated_at), instructions: bounded(item.instructions, 50000), result_json_schema: record(item.result_json_schema), result_template: template, items, skipped };
}
function parseReview(value: unknown, results: CustomerAiResults, ids: string[]): CustomerAiReview {
  const item = record(value), batchId = uuid(item.batch_id);
  if (batchId !== results.batch_id) throw new Error("The Customer AI review belongs to a different batch.");
  const items = list(item.items, value => {
    const row = record(value), status = statuses.find(status => status === row.status);
    if (!status) throw new Error("Invalid Customer AI review status.");
    const id = uuid(row.customer_id), result = row.result === null ? null : resultItem(row.result), original = results.items.find(item => item.customer_id === id);
    const currentDescription = nullableText(row.current_description, 20000), currentWebsite = nullableText(row.current_website, 2048);
    const proposedDescription = nullableText(row.proposed_description, 20000), proposedWebsite = row.proposed_website === null ? null : publicUrl(row.proposed_website);
    const sources = list(row.source_urls, publicUrl, 20), note = bounded(row.note, 2000), needsReview = boolean(row.needs_review);
    const applicable = boolean(row.applicable), replaceDescription = boolean(row.requires_description_overwrite), replaceWebsite = boolean(row.requires_website_overwrite);
    if (Boolean(result) !== Boolean(original) || (result && (result.customer_id !== id || !original || JSON.stringify(result) !== JSON.stringify(original))) ||
      (applicable !== ["READY", "OVERWRITE_REQUIRED"].includes(status)) || (applicable && (!result || needsReview || !(proposedDescription || proposedWebsite))) ||
      (result && (proposedDescription !== result.description || proposedWebsite !== result.website || note !== result.note || needsReview !== result.needs_review || JSON.stringify(sources) !== JSON.stringify(result.source_urls))) ||
      (applicable && (replaceDescription !== Boolean(currentDescription && proposedDescription && currentDescription !== proposedDescription) || replaceWebsite !== Boolean(currentWebsite && proposedWebsite && currentWebsite !== proposedWebsite))) ||
      (applicable && (status === "OVERWRITE_REQUIRED") !== (replaceDescription || replaceWebsite))) throw new Error("The Customer AI review does not match the imported results.");
    return { customer_id: id, name: bounded(row.name, 255), current_description: currentDescription, current_website: currentWebsite, proposed_description: proposedDescription, proposed_website: proposedWebsite,
      source_urls: sources, needs_review: needsReview, note, status, applicable, requires_description_overwrite: replaceDescription, requires_website_overwrite: replaceWebsite, result };
  });
  matchingIds(items.map(row => row.customer_id), ids);
  return { batch_id: batchId, items };
}
export const customerAiBriefClient = {
  async generate(selection: CustomerAiSelection) {
    const ids = selectedIds(selection.map(item => item.id));
    return parseBrief(await request("/customer-ai/brief", "POST", { selection }), ids);
  },
  async review(results: CustomerAiResults, selection = results.items.map(item => item.customer_id)) {
    const ids = selectedIds(selection), parsed = parseCustomerAiResults(results);
    if (parsed.items.some(item => !ids.includes(item.customer_id))) throw new Error("The Customer AI results include a customer outside this selection.");
    return parseReview(await request("/customer-ai/review", "POST", { results: parsed, selected_ids: ids }), parsed, ids);
  },
  async apply(id: string, payload: CustomerAiApply): Promise<CustomerAiReceipt> {
    const value = record(await request(`/customers/${uuid(id)}/ai-brief-result`, "POST", payload));
    const description = nullableText(value.description, 20000), website = nullableText(value.website, 2048);
    if (uuid(value.customer_id) !== id || uuid(value.batch_id) !== payload.batch_id || value.status !== "APPLIED" ||
      (payload.result.description !== null && description !== payload.result.description) || (payload.result.website !== null && website !== payload.result.website)) throw new Error("The Customer AI save response could not be verified.");
    return { customer_id: id, batch_id: payload.batch_id, status: "APPLIED", updated_at: date(value.updated_at), description, website };
  },
};
