import { request } from "./client";
import { nullable, record, string, uuid } from "./dto";
import { validateFolderPath } from "./folderPathValidation";

function nonnegative(value: unknown) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error("Invalid inventory number");
  return value;
}
function hash(value: unknown) {
  const result = string(value);
  if (!/^[0-9a-f]{64}$/.test(result)) throw new Error("Invalid revision hash");
  return result;
}
function review(value: unknown) {
  const data = record(value);
  return { generation: nonnegative(data.generation), revisionHash: data.revision_hash === null ? null : hash(data.revision_hash),
    inventoryId: data.inventory_id === null ? null : uuid(data.inventory_id), checkedAt: nullable(data.checked_at), failureCode: nullable(data.failure_code) };
}
export type MaterialReview = ReturnType<typeof review>;
function inventory(value: unknown) {
  if (value === null) return null;
  const data = record(value);
  if (data.schema_version !== 1 || !Array.isArray(data.entries) || data.entries.length > 20_000) throw new Error("Invalid source inventory");
  return { sourceHash: hash(data.source_revision_hash), totalBytes: nonnegative(data.total_bytes), masterResolution: nullable(data.master_resolution),
    entries: data.entries.map((value) => {
      const entry = record(value); const path = string(entry.path);
      if (validateFolderPath(path).error || !["file", "directory"].includes(string(entry.kind))) throw new Error("Unsafe inventory entry");
      return { path, kind: string(entry.kind), size: nonnegative(entry.size), sha256: entry.sha256 === null ? null : hash(entry.sha256) };
    }) };
}
export const reviewClient = {
  async current(id: string) {
    const data = record(await request(`/materials/${uuid(id)}/inventory`));
    return { review: review(data.review), inventory: inventory(data.inventory) };
  },
  async audit(id: string) {
    const data = await request(`/materials/${uuid(id)}/audit`);
    if (!Array.isArray(data)) throw new Error("Invalid audit history");
    return data.map((value) => { const item = record(value); const details = record(item.details);
      return { id: uuid(item.id), eventType: string(item.event_type), actorId: uuid(item.actor_id), createdAt: string(item.created_at), reason: typeof details.reason === "string" ? details.reason : null };
    });
  },
  async scan(id: string, generation: number, key: string) {
    return review(await request(`/materials/${uuid(id)}/inventory/scan`, "POST", { idempotency_key: uuid(key), expected_generation: generation }));
  },
  async reopen(id: string, generation: number, key: string, reason: string) {
    const result = record(await request(`/materials/${uuid(id)}/reopen`, "POST", { idempotency_key: uuid(key), expected_generation: generation, reason }));
    if (result.workflow_status !== "IN_PROGRESS") throw new Error("Invalid reopened state");
    return review(result.review);
  },
};
