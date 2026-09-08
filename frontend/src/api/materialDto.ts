import { boolean, nullable, record, string, uuid } from "./dto";

export const workflowStatuses = ["IN_PROGRESS", "DONE"] as const;
export const validationStatuses = ["NOT_CHECKED", "VALID", "WARNING", "ERROR", "METADATA_MISSING"] as const;
export const publicationStatuses = ["NOT_PUBLISHED", "PREPARING", "UPLOADED_WAITING_FOR_IMPORT", "WAITING_FOR_VERIFICATION", "PUBLISHED_CURRENT", "PUBLISHED_UPDATE_REQUIRED", "PUBLICATION_ERROR"] as const;
const userRoles = ["PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP", "ADMIN"] as const;
function choice<T extends string>(value: unknown, choices: readonly T[]): T {
  const match = choices.find((item) => item === value);
  if (match === undefined) throw new Error("Invalid API status or role");
  return match;
}
export interface MaterialCreateDto {
  project_id: string;
  published_brand_id: string;
  material_name: string;
  main_category_code: string;
  assigned_processor_id: string;
}
export type MaterialPatchDto = Partial<Omit<MaterialCreateDto, "published_brand_id">>;
export interface MaterialDto extends MaterialCreateDto {
  id: string;
  sequence_number: number;
  technical_identity: string;
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
  if (typeof n !== "number" || !Number.isInteger(n) || n < 1 || n > 9999)
    throw new Error("Invalid material sequence");
  return {
    id: uuid(v.id), project_id: uuid(v.project_id), published_brand_id: uuid(v.published_brand_id),
    material_name: string(v.material_name), main_category_code: string(v.main_category_code),
    assigned_processor_id: uuid(v.assigned_processor_id), sequence_number: n,
    technical_identity: string(v.technical_identity), folder_path: nullable(v.folder_path),
    workflow_status: choice(v.workflow_status, workflowStatuses),
    validation_status: choice(v.validation_status, validationStatuses),
    publication_status: choice(v.publication_status, publicationStatuses),
    is_published: boolean(v.is_published), created_at: string(v.created_at), updated_at: string(v.updated_at),
  };
}
export function materialFromDto(v: MaterialDto) {
  return {
    id: v.id, projectId: v.project_id, publishedBrandId: v.published_brand_id,
    materialName: v.material_name, mainCategoryCode: v.main_category_code,
    assignedProcessorId: v.assigned_processor_id, sequenceNumber: v.sequence_number,
    technicalIdentity: v.technical_identity, folderPath: v.folder_path,
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
