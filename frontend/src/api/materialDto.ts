import { boolean, nullable, record, string, uuid } from "./dto";

export const checkedStatuses = ["no", "OK", "Correction"] as const;
export const workflowStatuses = ["IN_PROGRESS", "DONE"] as const;
export const validationStatuses = ["NOT_CHECKED", "VALID", "WARNING", "ERROR", "METADATA_MISSING"] as const;
export const automaticFileCheckStatuses = ["NOT_CHECKED", "OK", "ISSUES"] as const;
export const publicationStatuses = ["NOT_PUBLISHED", "PREPARING", "UPLOADED_WAITING_FOR_IMPORT", "WAITING_FOR_VERIFICATION", "PUBLISHED_CURRENT", "PUBLISHED_UPDATE_REQUIRED", "PUBLICATION_ERROR"] as const;
const userRoles = ["PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP", "ADMIN"] as const;
function choice<T extends string>(value: unknown, choices: readonly T[]): T {
  const match = choices.find((item) => item === value);
  if (match === undefined) throw new Error("Invalid API status or role");
  return match;
}
export interface MaterialCreateDto {
  project_id: string | null;
  published_brand_id: string;
  material_name: string;
  main_category_code: string;
  assigned_processor_id: string;
  category_ids?: string[];
  collection_ids?: string[];
}
export type MaterialPatchDto = Partial<Omit<MaterialCreateDto, "published_brand_id" | "category_ids" | "collection_ids" | "assigned_processor_id" | "main_category_code">> & { assigned_processor_id?: string | null; main_category_code?: string | null };
export interface MaterialDto extends Omit<MaterialCreateDto, "project_id" | "published_brand_id" | "main_category_code" | "assigned_processor_id"> {
  is_draft?: boolean;
  published_brand_id: string | null;
  main_category_code: string | null;
  assigned_processor_id: string | null;
  automatic_file_check_status?: typeof automaticFileCheckStatuses[number];
  automatic_file_checked_at?: string | null;
  automatic_file_check_profile?: string | null;
  automatic_file_check_complete?: boolean;
  is_archived?: boolean;
  archived_at?: string | null;
  checked_status?: typeof checkedStatuses[number];
  note?: string | null;
  project_id: string | null;
  id: string;
  sequence_number: number | null;
  technical_identity: string | null;
  folder_path: string | null;
  workflow_status: typeof workflowStatuses[number];
  validation_status: typeof validationStatuses[number];
  publication_status: typeof publicationStatuses[number];
  is_published: boolean;
  created_at: string;
  updated_at: string;
}
export interface InternalUserDto {
  id: string;
  display_name: string;
  email: string;
  role: typeof userRoles[number];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}
export function parseMaterial(input: unknown): MaterialDto {
  const v = record(input);
  const n = v.sequence_number;
  const draft = v.is_draft === undefined ? false : boolean(v.is_draft);
  if (!(draft && n === null) && (typeof n !== "number" || !Number.isInteger(n) || n < 1 || n > 9999))
    throw new Error("Invalid material sequence");
  const brand = v.published_brand_id === null && draft ? null : uuid(v.published_brand_id);
  const category = v.main_category_code === null && draft ? null : string(v.main_category_code);
  const identity = v.technical_identity === null && draft ? null : string(v.technical_identity);
  if (draft && (n !== null || identity !== null || v.folder_path !== null || v.is_published !== false || (brand !== null && category !== null))) throw new Error("Invalid draft identity");
  return {
    ...(v.is_draft !== undefined ? { is_draft: draft } : {}),
    ...(v.automatic_file_check_status !== undefined ? { automatic_file_check_status: choice(v.automatic_file_check_status, automaticFileCheckStatuses) } : {}),
    ...(v.automatic_file_checked_at !== undefined ? { automatic_file_checked_at: nullable(v.automatic_file_checked_at) } : {}),
    ...(v.automatic_file_check_profile !== undefined ? { automatic_file_check_profile: nullable(v.automatic_file_check_profile) } : {}),
    ...(v.automatic_file_check_complete !== undefined ? { automatic_file_check_complete: boolean(v.automatic_file_check_complete) } : {}),
    ...(v.is_archived !== undefined ? { is_archived: boolean(v.is_archived) } : {}),
    ...(v.archived_at !== undefined ? { archived_at: nullable(v.archived_at) } : {}),
    ...(v.checked_status !== undefined ? { checked_status: choice(v.checked_status, checkedStatuses) } : {}),
    ...(v.note !== undefined ? { note: nullable(v.note) } : {}),
    id: uuid(v.id), project_id: v.project_id === null ? null : uuid(v.project_id), published_brand_id: brand,
    material_name: string(v.material_name), main_category_code: category,
    assigned_processor_id: v.assigned_processor_id === null ? null : uuid(v.assigned_processor_id), sequence_number: n as number | null,
    technical_identity: identity, folder_path: nullable(v.folder_path),
    workflow_status: choice(v.workflow_status, workflowStatuses),
    validation_status: choice(v.validation_status, validationStatuses),
    publication_status: choice(v.publication_status, publicationStatuses),
    is_published: boolean(v.is_published), created_at: string(v.created_at), updated_at: string(v.updated_at),
  };
}
export function materialFromDto<T extends MaterialDto>(v: T) {
  return {
    isDraft: v.is_draft ?? false,
    automaticFileCheckStatus: v.automatic_file_check_status ?? "NOT_CHECKED",
    automaticFileCheckedAt: v.automatic_file_checked_at ?? null,
    automaticFileCheckProfile: v.automatic_file_check_profile ?? null,
    automaticFileCheckComplete: v.automatic_file_check_complete ?? false,
    isArchived: v.is_archived ?? false, archivedAt: v.archived_at ?? null,
    checkedStatus: v.checked_status ?? "no", note: v.note ?? null,
    id: v.id, projectId: v.project_id, publishedBrandId: v.published_brand_id as T["published_brand_id"],
    materialName: v.material_name, mainCategoryCode: v.main_category_code as T["main_category_code"],
    assignedProcessorId: v.assigned_processor_id as T["assigned_processor_id"], sequenceNumber: v.sequence_number as T["sequence_number"],
    technicalIdentity: v.technical_identity as T["technical_identity"], folderPath: v.folder_path,
    workflowStatus: v.workflow_status, validationStatus: v.validation_status,
    publicationStatus: v.publication_status, isPublished: v.is_published,
    createdAt: v.created_at, updatedAt: v.updated_at,
  };
}
export type Material = ReturnType<typeof materialFromDto>;
export function parseInternalUser(input: unknown): InternalUserDto {
  const v = record(input);
  return {
    id: uuid(v.id), display_name: string(v.display_name), email: string(v.email),
    role: choice(v.role, userRoles), is_active: boolean(v.is_active),
    created_at: string(v.created_at), updated_at: string(v.updated_at),
  };
}
export function internalUserFromDto(v: InternalUserDto) {
  return { id: v.id, displayName: v.display_name, email: v.email, role: v.role,
    isActive: v.is_active, createdAt: v.created_at, updatedAt: v.updated_at };
}
export type InternalUser = ReturnType<typeof internalUserFromDto>;
export function isMaterialId(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}
export const statusLabel = (value: string) => value.toLowerCase().replaceAll("_", " ");
