import type { CompanyDto, PublishedBrandDto, ProjectDto } from "./dto";

export type CompanyWriteDto = Omit<
  CompanyDto,
  "id" | "created_at" | "updated_at"
>;
export type CompanyCreateDto = Pick<CompanyWriteDto, "name"> &
  Partial<Omit<CompanyWriteDto, "name">>;
export type CompanyPatchDto = Partial<CompanyWriteDto>;
export type BrandWriteDto = Omit<
  PublishedBrandDto,
  "id" | "created_at" | "updated_at" | "next_sequence_number"
>;
export type BrandCreateDto = Omit<BrandWriteDto, "is_active"> &
  Partial<Pick<BrandWriteDto, "is_active">>;
export type BrandPatchDto = Partial<BrandWriteDto>;
export type ProjectWriteDto = Omit<
  ProjectDto,
  "id" | "created_at" | "updated_at"
>;
export type ProjectCreateDto = Pick<
  ProjectWriteDto,
  "company_id" | "name" | "project_number"
> &
  Partial<Omit<ProjectWriteDto, "company_id" | "name" | "project_number">>;
export type ProjectPatchDto = Partial<ProjectWriteDto>;

export function changedFields<T extends object>(
  current: T,
  original: T,
): Partial<T> {
  const changes: Partial<T> = {};
  for (const key in current) {
    if (current[key] !== original[key]) changes[key] = current[key];
  }
  return changes;
}
