import { request } from "./client";
import { record, string, uuid } from "./dto";

export interface MaterialBatchCreate {
  idempotency_key: string; expected_paths_version: number; project_id: string | null;
  published_brand_id: string; assigned_processor_id: string; main_category_code: string;
  names: string[]; category_ids: string[]; collection_ids: string[]; template_name: string;
  /** Kept only when replaying a request saved by an older desktop version. */
  resolution?: number;
}
export interface MaterialCreationResult {
  id: string; status: "PENDING" | "PARTIAL" | "COMPLETED"; completedCount: number; totalCount: number;
  items: { materialId: string; name: string; identity: string; folderPath: string; status: string; errorCode: string | null }[];
}
function integer(value: unknown) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error("Invalid creation response");
  return value;
}
function result(input: unknown): MaterialCreationResult {
  const value = record(input);
  if (!["PENDING", "PARTIAL", "COMPLETED"].includes(String(value.status)) || !Array.isArray(value.items) || value.items.length > 100) throw new Error("Invalid creation result");
  return { id: uuid(value.id), status: value.status as MaterialCreationResult["status"], completedCount: integer(value.completed_count), totalCount: integer(value.total_count),
    items: value.items.map(input => { const item = record(input); return { materialId: uuid(item.material_id), name: string(item.name), identity: string(item.technical_identity),
      folderPath: string(item.folder_path), status: string(item.status), errorCode: item.error_code === null ? null : string(item.error_code) }; }) };
}
export const materialCreationClient = {
  async options() {
    const value = record(await request("/material-create-options"));
    if (!Array.isArray(value.templates)) throw new Error("Invalid template options");
    return { pathsVersion: integer(value.paths_version), templates: value.templates.map(input => { const item = record(input); return { name: string(item.name), sizeBytes: integer(item.size_bytes) }; }) };
  },
  async create(payload: MaterialBatchCreate) { return result(await request("/material-create-batches", "POST", payload)); },
  async lookup(key: string) { return result(await request(`/material-create-requests/${uuid(key)}`)); },
};

export interface BulkContentAdd {
  idempotency_key: string;
  materials: { id: string; expected_updated_at: string; expected_revision: number }[];
  category_ids: string[]; collection_ids: string[];
}
export const materialBulkContentClient = {
  async add(payload: BulkContentAdd) {
    const value = record(await request("/material-content-batches", "POST", payload));
    return { updatedCount: integer(value.updated_count) };
  },
};
