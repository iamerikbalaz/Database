import type { Company, PublishedBrand, Project, ProjectStatus } from "../types";
export type ProjectStatusDto = "NOT_STARTED" | "IN_PROGRESS" | "DONE";
export interface CompanyDto {
  id: string;
  name: string;
  legal_name: string | null;
  country: string | null;
  address: string | null;
  website: string | null;
  vat_id: string | null;
  notion_page_id: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}
export function companyFromDto(dto: CompanyDto): Company {
 return {
 id: dto.id,
 name: dto.name,
 officialName: dto.legal_name,
 country: dto.country,
 address: dto.address,
 websiteUrl: dto.website,
 vatId: dto.vat_id,
 notionPageId: dto.notion_page_id,
 status: dto.is_active ? "active" : "inactive",
 createdAt: dto.created_at,
 updatedAt: dto.updated_at,
 };
}
export function companyToDto(value: Company): CompanyDto {
 return {
 id: value.id,
 name: value.name,
 legal_name: value.officialName,
 country: value.country,
 address: value.address,
 website: value.websiteUrl,
 vat_id: value.vatId,
 notion_page_id: value.notionPageId,
 is_active: value.status === "active",
 created_at: value.createdAt,
 updated_at: value.updatedAt,
 };
}
export function parseCompany(input: unknown): CompanyDto {
 const value = record(input);
 return {
 id: string(value["id"]),
 name: string(value["name"]),
 legal_name: nullable(value["legal_name"]),
 country: nullable(value["country"]),
 address: nullable(value["address"]),
 website: nullable(value["website"]),
 vat_id: nullable(value["vat_id"]),
 notion_page_id: nullable(value["notion_page_id"]),
 is_active: boolean(value["is_active"]),
 created_at: string(value["created_at"]),
 updated_at: string(value["updated_at"]),
 };
}
export interface PublishedBrandDto {
  id: string;
  company_id: string;
  name: string;
  folder_prefix: string;
  brand_identifier: string;
  next_sequence_number: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}
export function publishedBrandFromDto(dto: PublishedBrandDto): PublishedBrand {
 return {
 id: dto.id,
 companyId: dto.company_id,
 name: dto.name,
 folderPrefix: dto.folder_prefix,
 brandIdentifier: dto.brand_identifier,
 nextSequenceNumber: dto.next_sequence_number,
 isActive: dto.is_active,
 createdAt: dto.created_at,
 updatedAt: dto.updated_at,
 };
}
export function publishedBrandToDto(value: PublishedBrand): PublishedBrandDto {
 return {
 id: value.id,
 company_id: value.companyId,
 name: value.name,
 folder_prefix: value.folderPrefix,
 brand_identifier: value.brandIdentifier,
 next_sequence_number: value.nextSequenceNumber,
 is_active: value.isActive,
 created_at: value.createdAt,
 updated_at: value.updatedAt,
 };
}
export function parsePublishedBrand(input: unknown): PublishedBrandDto {
 const value = record(input);
 return {
 id: string(value["id"]),
 company_id: string(value["company_id"]),
 name: string(value["name"]),
 folder_prefix: string(value["folder_prefix"]),
 brand_identifier: string(value["brand_identifier"]),
 next_sequence_number: sequence(value["next_sequence_number"]),
 is_active: boolean(value["is_active"]),
 created_at: string(value["created_at"]),
 updated_at: string(value["updated_at"]),
 };
}
export interface ProjectDto {
  id: string;
  company_id: string;
  project_number: string;
  name: string;
  status: ProjectStatusDto;
  due_date: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}
export function projectFromDto(dto: ProjectDto): Project {
 return {
 id: dto.id,
 companyId: dto.company_id,
 number: dto.project_number,
 name: dto.name,
 status: statusFromDto(dto.status),
 dueDate: dto.due_date,
 description: dto.notes,
 createdAt: dto.created_at,
 updatedAt: dto.updated_at,
 };
}
export function projectToDto(value: Project): ProjectDto {
 return {
 id: value.id,
 company_id: value.companyId,
 project_number: value.number,
 name: value.name,
 status: statusToDto(value.status),
 due_date: value.dueDate,
 notes: value.description,
 created_at: value.createdAt,
 updated_at: value.updatedAt,
 };
}
export function parseProject(input: unknown): ProjectDto {
 const value = record(input);
 return {
 id: string(value["id"]),
 company_id: string(value["company_id"]),
 project_number: string(value["project_number"]),
 name: string(value["name"]),
 status: projectStatus(value["status"]),
 due_date: nullable(value["due_date"]),
 notes: nullable(value["notes"]),
 created_at: string(value["created_at"]),
 updated_at: string(value["updated_at"]),
 };
}

function record(value: unknown): Record<string, unknown> {
 if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("Invalid API record");
 return Object.fromEntries(Object.entries(value));
}
function string(value: unknown): string { if (typeof value !== "string") throw new Error("Invalid API string"); return value; }
function nullable(value: unknown): string | null { return value === null ? null : string(value); }
function boolean(value: unknown): boolean { if (typeof value !== "boolean") throw new Error("Invalid API boolean"); return value; }
function sequence(value: unknown): number {
 if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value > 9999) throw new Error("Invalid brand sequence");
 return value;
}
function projectStatus(value: unknown): ProjectStatusDto {
 if (value !== "NOT_STARTED" && value !== "IN_PROGRESS" && value !== "DONE") throw new Error("Invalid project status");
 return value;
}
export function statusFromDto(value: ProjectStatusDto): ProjectStatus {
 switch (value) { case "NOT_STARTED": return "not_started"; case "IN_PROGRESS": return "in_progress"; case "DONE": return "done"; }
}
export function statusToDto(value: ProjectStatus): ProjectStatusDto {
 switch (value) { case "not_started": return "NOT_STARTED"; case "in_progress": return "IN_PROGRESS"; case "done": return "DONE"; }
}
export function parseList<T>(value: unknown, parse: (item: unknown) => T): T[] {
 if (!Array.isArray(value)) throw new Error("Invalid API list");
 return value.map(parse);
}
