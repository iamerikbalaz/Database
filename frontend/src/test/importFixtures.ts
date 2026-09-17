import { companyToDto } from "../api/dto";
import { companies } from "../api/mockData";
import { materialBrand, materialProject, processorDto } from "./materialFixtures";

export const importCompanies = companies.map(companyToDto).map((item) => ({ ...item, is_active: true }));
export const importColumns = { identity: "Identity", name: "Name", project: "Project", brand: "Brand", processor: "Processor" };
export const importInspection = {
  format: "CSV", sheets: [], requires_sheet: false, source_sha256: "a".repeat(64), headers: Object.values(importColumns), row_count: 1,
  sample: [{ row: 2, values: ["LASVIT_0007_G03", "Synthetic material", "Project A", "Brand A", "Processor A"] }],
};
export const importMappings = { project: ["Project A"], brand: ["Brand A"], processor: ["Processor A"] };
export const importReferences = { projects: [materialProject], brands: [materialBrand], processors: [processorDto], companies: importCompanies };
export const importRow = { source_row: 2, technical_identity: "LASVIT_0007_G03", material_name: "Synthetic material", sequence_number: 7,
  main_category_code: "G03", project_id: materialProject.id, published_brand_id: materialBrand.id, assigned_processor_id: processorDto.id };
export const importInitialState = { workflow_status: "IN_PROGRESS", validation_status: "NOT_CHECKED", publication_status: "NOT_PUBLISHED", is_published: false, folder_path: null };
export const importSnapshot = { schema_version: 1, source_sha256: "a".repeat(64), rows: [importRow], references: importReferences,
  initial_state: importInitialState, ignored_columns: [] };
export const importPreview = { can_confirm: true, preview_hash: "b".repeat(64), row_count: 1, findings: [], snapshot: importSnapshot,
  warnings: ["IMPORT_REQUIRES_NORMAL_REVIEW"] };
export const importBatch = { id: "71000000-0000-4000-8000-000000000001", actor_id: processorDto.id,
  idempotency_key: "72000000-0000-4000-8000-000000000001", source_sha256: "a".repeat(64), preview_hash: "b".repeat(64),
  source_format: "CSV", row_count: 1, reason: "Reviewed synthetic history", created_at: "2026-09-17T16:00:00Z", snapshot: importSnapshot,
  rows: [{ ...importRow, ...importInitialState, material_id: "73000000-0000-4000-8000-000000000001" }] };
