import { request } from "./client";
import { boolean, nullable, record, string, uuid } from "./dto";

type Value = string | number | boolean | null;
type Field = [string, (value: unknown) => Value];
const nullableUuid = (value: unknown) => value === null ? null : uuid(value);
const sequence = (value: unknown) => {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1 || value > 2147483647) throw new Error("Invalid history number");
  return value;
};
export const resourceHistorySchema: Record<ResourceKind, Record<string, Field>> = {
  BRAND: { company_id: ["Company ID", uuid], name: ["Name", string], folder_prefix: ["Folder prefix", string],
    brand_identifier: ["Brand identifier", string], is_active: ["Active", boolean] },
  PROJECT: { company_id: ["Company ID", uuid], project_number: ["Project number", string], name: ["Name", string],
    status: ["Status", string], due_date: ["Deadline", nullable], notes: ["Notes", nullable] },
  USER: { display_name: ["Display name", string], email: ["Email", string], role: ["Role", string], is_active: ["Active", boolean] },
  MATERIAL: { project_id: ["Project ID", (value) => value === null ? null : uuid(value)], published_brand_id: ["Published brand ID", uuid], sequence_number: ["Sequence number", sequence],
    material_name: ["Material name", string], main_category_code: ["Main category", string], assigned_processor_id: ["Assigned processor ID", nullableUuid],
    technical_identity: ["Technical identity", string], folder_path: ["Folder path", nullable], workflow_status: ["Workflow status", string],
    validation_status: ["Validation status", string], is_published: ["Published", boolean], publication_status: ["Publication status", string] },
};
export type ResourceKind = "BRAND" | "PROJECT" | "USER" | "MATERIAL";
export const resourceHistoryNames: Record<ResourceKind, string> = { BRAND: "Brand", PROJECT: "Project", USER: "Account profile", MATERIAL: "Material record" };
const segments: Record<ResourceKind, string> = { BRAND: "brands", PROJECT: "projects", USER: "internal-users", MATERIAL: "materials" };
function digest(input: unknown) { const result = string(input); if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid history digest"); return result; }
function snapshot(input: unknown, kind: ResourceKind, id: string): Record<string, Value> {
  const value = record(input), schema = resourceHistorySchema[kind];
  if (uuid(value.id) !== id || Object.keys(value).sort().join() !== ["id", ...Object.keys(schema)].sort().join()) throw new Error("Invalid history snapshot");
  return { id, ...Object.fromEntries(Object.entries(schema).map(([field, [, parse]]) => [field, parse(value[field])])) };
}
function change(input: unknown, kind: ResourceKind, id: string) {
  const value = record(input), before = record(value.before), version = sequence(value.version);
  if (value.resource_kind !== kind || uuid(value.resource_id) !== id || !["CREATED", "UPDATED"].includes(string(value.action))) throw new Error("Invalid history event");
  if (value.action === "CREATED" && (version !== 1 || Object.keys(before).length)) throw new Error("Invalid creation history");
  const previous = value.action === "CREATED" ? null : snapshot(before, kind, id), current = snapshot(value.after, kind, id);
  const changed = Object.keys(resourceHistorySchema[kind]).filter((field) => !previous || previous[field] !== current[field]);
  const createdAt = string(value.created_at);
  if (!changed.length || !/T.*(?:Z|[+-]\d\d:\d\d)$/.test(createdAt) || !Number.isFinite(Date.parse(createdAt))) throw new Error("Invalid history change");
  return { id: uuid(value.id), actorId: uuid(value.actor_id), version, action: value.action as "CREATED" | "UPDATED",
    before: previous, after: current, changed, beforeHash: digest(value.before_sha256), afterHash: digest(value.after_sha256), createdAt };
}
export function resourceHistoryPage(input: unknown, kind: ResourceKind, id: string, after: string | null = null) {
  const value = record(input); id = uuid(id);
  if (value.resource_kind !== kind || uuid(value.resource_id) !== id || !Array.isArray(value.items) || value.items.length > 20) throw new Error("Invalid history page");
  const items = value.items.map((item) => change(item, kind, id)), nextCursor = value.next_cursor === null ? null : uuid(value.next_cursor);
  if (new Set(items.map((item) => item.id)).size !== items.length || items.some((item, index) => item.id === after || index > 0 && items[index - 1].version !== item.version + 1)
      || nextCursor && (items.length !== 20 || nextCursor !== items.at(-1)?.id || nextCursor === after)) throw new Error("Invalid history ordering");
  return { items, nextCursor };
}
export const resourceHistoryClient = {
  async history(kind: ResourceKind, id: string, after: string | null = null) {
    return resourceHistoryPage(await request(`/${segments[kind]}/${uuid(id)}/history${after ? `?after=${uuid(after)}` : ""}`), kind, id, after);
  },
};
