import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";

export const AI_BRIEF_MAX_BYTES = 5 * 1024 * 1024;
export type AiBriefSelection = { id: string; expected_updated_at: string }[];
export type AiBriefResultItem = { material_id: string; context_hash: string; content_revision: number; description: string | null; source_urls: string[]; needs_review: boolean; note: string; tags?: string[] };
export type AiBriefResults = { schema_version: "reawote-ai-results-v1" | "reawote-ai-results-v2"; batch_id: string; items: AiBriefResultItem[] };
export type AiBrief = { schema_version: "reawote-ai-brief-v1" | "reawote-ai-brief-v2"; batch_id: string; generated_at: string; instructions: string; result_json_schema: Record<string, unknown>; result_template: AiBriefResults;
  items: { material_id: string; context_hash: string; content_revision: number; context: { name: string; customer: { id: string; name: string; website: string | null }; categories: { id: string; value: string }[]; collections: { id: string; value: string }[]; source_urls: { id: string; url: string }[]; current_description: string | null; current_tags?: string[] } }[];
  skipped: { material_id: string; code: string; message: string }[] };
const reviewStatuses = ["READY", "OVERWRITE_REQUIRED", "STALE_CONTEXT", "NEEDS_REVIEW", "EMPTY_DESCRIPTION", "MISSING_RESULT", "MATERIAL_IDENTITY_INCOMPLETE", "CUSTOMER_REQUIRED", "CUSTOMER_INACTIVE", "MATERIAL_BUSY", "ALREADY_APPLIED", "MATERIAL_UNAVAILABLE", "TAG_LIMIT_EXCEEDED"] as const;
export type AiBriefReviewItem = { material_id: string; name: string; current_description: string | null; proposed_description: string | null; current_tags?: string[]; proposed_tags?: string[]; merged_tags?: string[]; source_urls: string[]; needs_review: boolean; note: string; status: typeof reviewStatuses[number]; applicable: boolean; requires_overwrite: boolean; result: AiBriefResultItem | null };
export type AiBriefReview = { batch_id: string; items: AiBriefReviewItem[] };
export type AiBriefApply = { idempotency_key: string; batch_id: string; result: AiBriefResultItem; overwrite: boolean };
export type AiBriefReceipt = { material_id: string; batch_id: string; draft_id: string; status: "APPLIED"; content_revision: number; description: string; tags?: string[] };

function bounded(value: unknown, max: number) { const result = string(value); if (result.length > max) throw new Error("AI brief text is too long."); return result; }
function description(value: unknown) { return value === null ? null : bounded(value, 10000); }
function integer(value: unknown) { if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > 2147483647) throw new Error("Invalid AI brief revision."); return value; }
function hash(value: unknown) { const result = string(value); if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid AI brief context."); return result; }
function list<T>(value: unknown, parse: (item: unknown) => T, max = 100): T[] { if (!Array.isArray(value) || value.length > max) throw new Error("Invalid AI brief list."); return value.map(parse); }
function unique(ids: string[]) { if (new Set(ids).size !== ids.length) throw new Error("AI brief contains duplicate materials."); }
function exactKeys(item: Record<string, unknown>, keys: string[]) { if (Object.keys(item).length !== keys.length || Object.keys(item).some(key => !keys.includes(key))) throw new Error("The AI results contain missing or unexpected fields."); }
function publicUrl(value: unknown) {
  const raw = bounded(value, 2048);
  try {
    const url = new URL(raw), host = url.hostname.toLowerCase();
    if (!raw.startsWith("https://") || url.protocol !== "https:" || url.username || url.password || raw.split("/")[2]?.includes("@") || url.port || /[?#]/.test(raw) || /[\p{C}\s\\]/u.test(decodeURIComponent(raw)) ||
      host.length > 253 || !host.includes(".") || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".internal") ||
      !/^[a-z][a-z0-9-]{1,62}$/.test(host.split(".").at(-1)!) || host.split(".").some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))) throw new Error();
    // Preserve path segments like the server's URL sanitizer; URL.pathname
    // would resolve dot segments before the reviewed request is recorded.
    const path = raw.match(/^https:\/\/[^/]+(\/.*)?$/)?.[1] || "/";
    const encodedPath = [...path].map(char => /[A-Za-z0-9/%:@!$&'()*+,;=\-._~]/.test(char) ? char : encodeURIComponent(char)).join("");
    const normalized = `https://${host}${encodedPath}`;
    if (normalized.length > 2048) throw new Error();
    return normalized;
  } catch { throw new Error("AI sources must be public HTTPS addresses without credentials, query strings or fragments."); }
}
function plainText(value: unknown, max: number) { const text = bounded(value, max).trim(); if ([...text].some(char => /\p{C}/u.test(char) && !"\n\r\t".includes(char))) throw new Error("AI results contain unsupported control characters."); return text.normalize("NFC").replace(/\r\n?/g, "\n"); }
function tags(value: unknown, max = 100) { return list(value, value => { const text = string(value); if (/[\p{C}:]/u.test(text)) throw new Error("AI tags cannot contain colons or control characters."); const normalized = text.normalize("NFC").trim().replace(/\s+/g, " "); if (!normalized || normalized.length > 100) throw new Error("AI tags must contain 1–100 characters."); return normalized; }, max); }
function resultItem(value: unknown, withTags = Object.hasOwn(record(value), "tags")): AiBriefResultItem {
  const item = record(value); exactKeys(item, ["material_id", "context_hash", "content_revision", "description", "source_urls", "needs_review", "note", ...(withTags ? ["tags"] : [])]);
  const sources = list(item.source_urls, publicUrl, 20);
  if (new Set(sources).size !== sources.length) throw new Error("AI results contain duplicate source URLs.");
  return { material_id: uuid(item.material_id), context_hash: hash(item.context_hash), content_revision: integer(item.content_revision), description: item.description === null ? null : plainText(item.description, 10000), source_urls: sources, needs_review: boolean(item.needs_review), note: plainText(item.note, 2000), ...(withTags ? { tags: tags(item.tags) } : {}) };
}
export function parseAiBriefResults(value: unknown): AiBriefResults {
  const item = record(value); exactKeys(item, ["schema_version", "batch_id", "items"]);
  if (item.schema_version !== "reawote-ai-results-v1" && item.schema_version !== "reawote-ai-results-v2") throw new Error("This file is not an AI results JSON file.");
  const items = list(item.items, value => resultItem(value, item.schema_version === "reawote-ai-results-v2")); unique(items.map(row => row.material_id));
  return { schema_version: item.schema_version, batch_id: uuid(item.batch_id), items };
}
export function parseAiBriefResultsText(value: string) {
  if (new TextEncoder().encode(value).byteLength > AI_BRIEF_MAX_BYTES) throw new Error("Choose a JSON file no larger than 5 MiB.");
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { throw new Error("The selected file is not valid JSON."); }
  return parseAiBriefResults(parsed);
}
function selectedIds(ids: string[]) {
  const result = list(ids, uuid); unique(result);
  if (!result.length) throw new Error("Select between 1 and 100 distinct materials.");
  return result;
}
function matchingIds(actual: string[], expected: string[]) { unique(actual); if (actual.length !== expected.length || actual.some(id => !expected.includes(id))) throw new Error("The AI response does not match the selected materials."); }
function named(value: unknown) { const item = record(value); return { id: uuid(item.id), value: bounded(item.value, 255) }; }
function parseBrief(value: unknown, ids: string[]): AiBrief {
  const item = record(value);
  if (item.schema_version !== "reawote-ai-brief-v1" && item.schema_version !== "reawote-ai-brief-v2") throw new Error("Invalid AI brief format.");
  const batchId = uuid(item.batch_id), template = parseAiBriefResults(item.result_template);
  if (template.batch_id !== batchId) throw new Error("The AI brief template belongs to a different batch.");
  const items = list(item.items, value => {
    const row = record(value), context = record(row.context), customer = record(context.customer);
    return { material_id: uuid(row.material_id), context_hash: hash(row.context_hash), content_revision: integer(row.content_revision), context: {
      name: bounded(context.name, 255), customer: { id: uuid(customer.id), name: bounded(customer.name, 255), website: customer.website === null ? null : publicUrl(customer.website) },
      categories: list(context.categories, named), collections: list(context.collections, named), source_urls: list(context.source_urls, value => { const source = record(value); return { id: uuid(source.id), url: publicUrl(source.url) }; }, 20), current_description: description(context.current_description), ...(context.current_tags !== undefined ? { current_tags: tags(context.current_tags) } : {}),
    } };
  });
  const skipped = list(item.skipped, value => { const row = record(value); return { material_id: uuid(row.material_id), code: bounded(row.code, 100), message: bounded(row.message, 2000) }; });
  matchingIds([...items.map(row => row.material_id), ...skipped.map(row => row.material_id)], ids);
  matchingIds(template.items.map(row => row.material_id), items.map(row => row.material_id));
  if (template.items.some(row => { const source = items.find(item => item.material_id === row.material_id)!; return row.context_hash !== source.context_hash || row.content_revision !== source.content_revision; })) throw new Error("The AI brief template has a different context.");
  const generatedAt = string(item.generated_at); if (!Number.isFinite(Date.parse(generatedAt))) throw new Error("Invalid AI brief date.");
  return { schema_version: item.schema_version, batch_id: batchId, generated_at: generatedAt, instructions: bounded(item.instructions, 50000), result_json_schema: record(item.result_json_schema), result_template: template, items, skipped };
}
function parseReview(value: unknown, results: AiBriefResults, ids: string[]): AiBriefReview {
  const item = record(value), batchId = uuid(item.batch_id);
  if (batchId !== results.batch_id) throw new Error("The AI review belongs to a different batch.");
  const items = list(item.items, value => {
    const row = record(value), status = reviewStatuses.find(status => status === row.status);
    if (!status) throw new Error("Invalid AI review status.");
    const result = row.result === null ? null : resultItem(row.result), id = uuid(row.material_id), original = results.items.find(item => item.material_id === id);
    const applicable = boolean(row.applicable), overwrite = boolean(row.requires_overwrite), current = description(row.current_description), proposed = description(row.proposed_description);
    const sources = list(row.source_urls, publicUrl, 20), needsReview = boolean(row.needs_review), note = bounded(row.note, 2000);
    if ((result && (result.material_id !== id || !original || JSON.stringify(result) !== JSON.stringify(original))) ||
      (applicable !== ["READY", "OVERWRITE_REQUIRED"].includes(status)) || (applicable && (!result || needsReview || !proposed?.trim())) ||
      (result && (proposed !== result.description || needsReview !== result.needs_review || note !== result.note || JSON.stringify(sources) !== JSON.stringify(result.source_urls))) ||
      (applicable && overwrite !== Boolean(current?.trim())) || (status === "OVERWRITE_REQUIRED" && !overwrite)) throw new Error("The AI review does not match the imported results.");
    const tagFields = row.current_tags !== undefined ? { current_tags: tags(row.current_tags), proposed_tags: tags(row.proposed_tags), merged_tags: tags(row.merged_tags, 200) } : {};
    if (result?.tags && JSON.stringify(tagFields.proposed_tags) !== JSON.stringify(result.tags)) throw new Error("The AI review tags do not match the imported results.");
    return { material_id: id, name: bounded(row.name, 255), current_description: current, proposed_description: proposed, ...tagFields, source_urls: sources, needs_review: needsReview, note, status, applicable, requires_overwrite: overwrite, result };
  });
  matchingIds(items.map(row => row.material_id), ids);
  return { batch_id: batchId, items };
}
export const aiBriefClient = {
  async generate(selection: AiBriefSelection) {
    const ids = selectedIds(selection.map(row => row.id));
    return parseBrief(await request("/material-ai/brief", "POST", { selection }), ids);
  },
  async review(results: AiBriefResults, selection = results.items.map(item => item.material_id)) {
    const ids = selectedIds(selection), parsed = parseAiBriefResults(results);
    if (parsed.items.some(item => !ids.includes(item.material_id))) throw new Error("The AI results include a material outside this selection.");
    return parseReview(await request("/material-ai/review", "POST", { results: parsed, selected_ids: ids }), parsed, ids);
  },
  async apply(id: string, payload: AiBriefApply): Promise<AiBriefReceipt> {
    const value = record(await request(`/materials/${uuid(id)}/ai-brief-result`, "POST", payload));
    if (uuid(value.material_id) !== id || uuid(value.batch_id) !== payload.batch_id || value.status !== "APPLIED" || integer(value.content_revision) !== payload.result.content_revision + 1 || description(value.description) !== payload.result.description) throw new Error("The AI save response could not be verified.");
    return { material_id: id, batch_id: payload.batch_id, draft_id: uuid(value.draft_id), status: "APPLIED", content_revision: value.content_revision as number, description: string(value.description), ...(value.tags !== undefined ? { tags: tags(value.tags) } : {}) };
  },
};
