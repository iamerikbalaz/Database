import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";

export const notionFields = ["address", "country", "legal_name", "name", "vat_id", "website"] as const;
export type NotionField = typeof notionFields[number];
export const notionLabels: Record<NotionField, string> = {
  name: "Name", legal_name: "Official name", country: "Country", address: "Address", vat_id: "VAT ID", website: "Website",
};
function field(value: unknown): NotionField {
  if (typeof value !== "string" || !notionFields.includes(value as NotionField)) throw new Error("Invalid company field");
  return value as NotionField;
}
function hash(value: unknown) {
  const result = string(value); if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid comparison digest"); return result;
}
function identity(value: unknown) {
  const result = uuid(value); if (result === "00000000-0000-0000-0000-000000000000") throw new Error("Invalid Notion ID"); return result;
}
export function notionPageId(value: string | null): string | null {
  if (value === null || !/^(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$/i.test(value)) return null;
  const compact = value.replaceAll("-", "");
  try { return identity(`${compact.slice(0, 8)}-${compact.slice(8, 12)}-${compact.slice(12, 16)}-${compact.slice(16, 20)}-${compact.slice(20)}`); }
  catch { return null; }
}
function ordered(fields: NotionField[], empty = false) {
  if ((!empty && !fields.includes("name")) || fields.length > 6 || fields.join() !== [...new Set(fields)].sort().join()) throw new Error("Invalid company mapping");
  return fields;
}
export function notionConfiguration(input: unknown) {
  const value = record(input); const enabled = boolean(value.enabled);
  if (value.direction !== "READ_ONLY" || value.resource !== "COMPANY" || !Array.isArray(value.mapped_fields)) throw new Error("Invalid Notion configuration");
  return { enabled, fields: ordered(value.mapped_fields.map(field), !enabled) };
}
export type NotionConfiguration = ReturnType<typeof notionConfiguration>;
export function notionComparison(input: unknown, companyId: string, pageId: string, fields: NotionField[]) {
  const value = record(input), source = record(value.source);
  if (value.direction !== "READ_ONLY" || uuid(value.company_id) !== uuid(companyId) || identity(source.page_id) !== identity(pageId)
      || !Array.isArray(value.fields) || value.fields.length > 6) throw new Error("Unrelated Notion comparison");
  const rows = value.fields.map((input) => {
    const row = record(input), key = field(row.field);
    const current = row.current === null ? null : string(row.current);
    const observed = row.observed === null ? null : string(row.observed);
    const limit = key === "address" ? 5000 : key === "website" ? 2048 : ["country", "vat_id"].includes(key) ? 100 : 255;
    if (observed !== null && (observed.length > limit || [...observed].some((char) => char.charCodeAt(0) < 32 && !"\n\r\t".includes(char) || char.charCodeAt(0) === 127))
        || key === "name" && (!current || !observed) || boolean(row.changed) !== (current !== observed)) throw new Error("Invalid comparison value");
    return { field: key, current, observed, changed: boolean(row.changed) };
  });
  if (ordered(rows.map((row) => row.field)).join() !== fields.join()) throw new Error("Changed Notion mapping");
  const editedAt = string(source.last_edited_time);
  if (editedAt.length > 64 || !/T.*(?:Z|[+-]\d\d:\d\d)$/.test(editedAt) || !Number.isFinite(Date.parse(editedAt))) throw new Error("Invalid Notion timestamp");
  return { companyId: uuid(companyId), pageId: identity(pageId), dataSourceId: identity(source.data_source_id), databaseId: identity(source.database_id),
    editedAt, localHash: hash(value.local_sha256), mappingHash: hash(source.mapping_sha256), observationHash: hash(source.observation_sha256), rows };
}
export type NotionComparison = ReturnType<typeof notionComparison>;
export const notionClient = {
  async configuration() { return notionConfiguration(await request("/integrations/notion")); },
  async compare(companyId: string, pageId: string, fields: NotionField[]) {
    const selected = notionPageId(pageId); if (!selected) throw new Error("Invalid linked page");
    return notionComparison(await request(`/companies/${uuid(companyId)}/notion-preview`, "POST", { expected_page_id: selected }), companyId, selected, fields);
  },
};
