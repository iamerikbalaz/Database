import { request } from "./client";
import { boolean, nullable, record, string, uuid } from "./dto";

export interface MaterialBatchCreate {
  idempotency_key: string; expected_paths_version: number | null; project_id: string | null;
  published_brand_id: string | null; assigned_processor_id: string | null; main_category_code: string | null;
  names: string[]; category_ids: string[]; collection_ids: string[]; template_name: string | null;
  /** Kept only when replaying a request saved by an older desktop version. */
  resolution?: number;
}
export interface MaterialCreationResult {
  id: string; status: "PENDING" | "PARTIAL" | "COMPLETED"; completedCount: number; totalCount: number;
  items: { materialId: string; name: string; identity: string | null; folderPath: string | null; status: string; errorCode: string | null }[];
}
function integer(value: unknown) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error("Invalid creation response");
  return value;
}
function result(input: unknown): MaterialCreationResult {
  const value = record(input);
  if (!["PENDING", "PARTIAL", "COMPLETED"].includes(String(value.status)) || !Array.isArray(value.items) || value.items.length > 100) throw new Error("Invalid creation result");
  return { id: uuid(value.id), status: value.status as MaterialCreationResult["status"], completedCount: integer(value.completed_count), totalCount: integer(value.total_count),
    items: value.items.map(input => { const item = record(input); return { materialId: uuid(item.material_id), name: string(item.name), identity: nullable(item.technical_identity),
      folderPath: nullable(item.folder_path), status: string(item.status), errorCode: item.error_code === null ? null : string(item.error_code) }; }) };
}
export interface MaterialFolderCreate { idempotency_key: string; expected_updated_at: string; expected_paths_version: number | null; template_name: string | null; }
export const materialCreationClient = {
  async options(): Promise<{ pathsVersion: number; templatesAvailable?: boolean; canCreateFolders?: boolean; templates: { name: string; sizeBytes: number }[] }> {
    const value = record(await request("/material-create-options"));
    if (!Array.isArray(value.templates)) throw new Error("Invalid template options");
    return { pathsVersion: integer(value.paths_version), templatesAvailable: value.templates_available === undefined || boolean(value.templates_available), canCreateFolders: value.can_create_folders === undefined || boolean(value.can_create_folders), templates: value.templates.map(input => { const item = record(input); return { name: string(item.name), sizeBytes: integer(item.size_bytes) }; }) };
  },
  async create(payload: MaterialBatchCreate) { return result(await request("/material-create-batches", "POST", payload)); },
  async lookup(key: string) { return result(await request(`/material-create-requests/${uuid(key)}`)); },
  async createFolder(id: string, payload: MaterialFolderCreate) { return result(await request(`/materials/${uuid(id)}/create-folder`, "POST", payload)); },
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
