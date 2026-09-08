import type { InternalUserDto, MaterialDto } from "../api/materialDto";
import { projectToDto, publishedBrandToDto } from "../api/dto";
import { brands, projects } from "../api/mockData";

export const materialProject = projectToDto(projects[0]);
// Deliberately a different company from the project.
export const materialBrand = { ...publishedBrandToDto(brands[1]), next_sequence_number: 10000 };
export const processorDto: InternalUserDto = {
  id: "40000000-0000-4000-8000-000000000001", display_name: "Active Processor",
  email: "processor@example.test", role: "PROCESSOR", is_active: true,
  created_at: "2026-09-01T12:00:00Z", updated_at: "2026-09-02T12:00:00Z",
};
export const inactiveDto: InternalUserDto = {
  ...processorDto, id: "40000000-0000-4000-8000-000000000002", display_name: "Inactive Processor", is_active: false,
};
export const materialDto: MaterialDto = {
  id: "50000000-0000-4000-8000-000000000001", project_id: materialProject.id,
  published_brand_id: materialBrand.id, assigned_processor_id: processorDto.id,
  sequence_number: 9999, technical_identity: "LASVIT_9999_G03", material_name: "Crystal surface",
  main_category_code: "G03", folder_path: null, workflow_status: "IN_PROGRESS",
  validation_status: "NOT_CHECKED", publication_status: "NOT_PUBLISHED", is_published: false,
  created_at: "2026-09-01T12:00:00Z", updated_at: "2026-09-02T12:00:00Z",
};
