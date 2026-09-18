import { request } from "./client";
import { boolean, nullable, record, string, uuid } from "./dto";

export const companyHistoryLabels = { name: "Name", legal_name: "Official name", country: "Country", address: "Address",
  website: "Website", vat_id: "VAT ID", notion_page_id: "Notion page ID", is_active: "Active" };
export type CompanyHistoryField = keyof typeof companyHistoryLabels;
const fields = Object.keys(companyHistoryLabels) as CompanyHistoryField[];
function digest(input: unknown) { const result = string(input); if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid company history digest"); return result; }
function snapshot(input: unknown, companyId: string) {
  const value = record(input);
  if (uuid(value.id) !== companyId || Object.keys(value).sort().join() !== ["id", ...fields].sort().join()) throw new Error("Invalid company history snapshot");
  return { id: companyId, name: string(value.name), legal_name: nullable(value.legal_name), country: nullable(value.country),
    address: nullable(value.address), website: nullable(value.website), vat_id: nullable(value.vat_id), notion_page_id: nullable(value.notion_page_id), is_active: boolean(value.is_active) };
}
function event(input: unknown, companyId: string) {
  const value = record(input), before = record(value.before);
  if (uuid(value.company_id) !== companyId || !["CREATED", "UPDATED", "NOTION_ADOPTED"].includes(string(value.action))
      || typeof value.version !== "number" || !Number.isSafeInteger(value.version) || value.version < 1 || value.version > 2147483647) throw new Error("Invalid company history event");
  if (value.action === "CREATED" && (value.version !== 1 || Object.keys(before).length)) throw new Error("Invalid company creation history");
  const previous = value.action === "CREATED" ? null : snapshot(before, companyId), current = snapshot(value.after, companyId);
  const changed = fields.filter((field) => !previous || previous[field] !== current[field]);
  if (!changed.length) throw new Error("Invalid empty company change");
  const createdAt = string(value.created_at);
  if (!/T.*(?:Z|[+-]\d\d:\d\d)$/.test(createdAt) || !Number.isFinite(Date.parse(createdAt))) throw new Error("Invalid company history date");
  // This history view exposes only whitelisted company snapshots and safe event
  // metadata. Remote provenance remains available in the authenticated API.
  return { id: uuid(value.id), companyId, actorId: uuid(value.actor_id), version: value.version, action: value.action as "CREATED" | "UPDATED" | "NOTION_ADOPTED",
    before: previous, after: current, changed, beforeHash: digest(value.before_sha256), afterHash: digest(value.after_sha256), reason: string(value.reason), createdAt };
}
export type CompanyChange = ReturnType<typeof event>;
export function companyHistoryPage(input: unknown, companyId: string, after: string | null = null) {
  const value = record(input); companyId = uuid(companyId);
  if (uuid(value.company_id) !== companyId || !Array.isArray(value.items) || value.items.length > 20) throw new Error("Invalid company history page");
  const items = value.items.map((item) => event(item, companyId)), nextCursor = value.next_cursor === null ? null : uuid(value.next_cursor);
  if (new Set(items.map((item) => item.id)).size !== items.length || items.some((item, index) => item.id === after || index > 0 && items[index - 1].version !== item.version + 1)
      || nextCursor && (items.length !== 20 || nextCursor !== items.at(-1)?.id || nextCursor === after)) throw new Error("Invalid company history ordering");
  return { items, nextCursor };
}
export const companyHistoryClient = {
  async history(companyId: string, after: string | null = null) {
    return companyHistoryPage(await request(`/companies/${uuid(companyId)}/history${after ? `?after=${uuid(after)}` : ""}`), companyId, after);
  },
};
