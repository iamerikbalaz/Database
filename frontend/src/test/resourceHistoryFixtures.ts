import type { ResourceKind } from "../api/resourceHistoryClient";
export const historyResourceId = "11111111-1111-4111-8111-111111111111";
const relatedId = "22222222-2222-4222-8222-222222222222";
export function resourceHistoryDto(kind: ResourceKind = "PROJECT", count = 2, id = historyResourceId) {
  const values = {
    BRAND: { company_id: relatedId, name: "Synthetic brand", folder_prefix: "TEST", brand_identifier: "brand", is_active: true },
    PROJECT: { company_id: relatedId, project_number: "0001", name: "Synthetic project", status: "NOT_STARTED", due_date: null, notes: null },
    USER: { display_name: "Synthetic user", email: "user@example.invalid", role: "PROCESSOR", is_active: true },
    MATERIAL: { project_id: relatedId, published_brand_id: relatedId, sequence_number: 1, material_name: "Synthetic material", main_category_code: "WOOD",
      assigned_processor_id: relatedId, technical_identity: "TEST_0001_WOOD", folder_path: null, workflow_status: "NOT_STARTED", validation_status: "NOT_CHECKED", is_published: false, publication_status: "NOT_PUBLISHED" },
  };
  const name = kind === "USER" ? "display_name" : kind === "MATERIAL" ? "material_name" : "name";
  const after: Record<string, unknown> = { id, ...values[kind] };
  const items = Array.from({ length: count }, (_, index) => {
    const version = count - index;
    return { id: `44444444-4444-4444-8444-${String(version).padStart(12, "0")}`, resource_kind: kind as string, resource_id: id,
      actor_id: "55555555-5555-4555-8555-555555555555", version, action: version === 1 ? "CREATED" : "UPDATED",
      before: version === 1 ? {} : { ...after, [name]: "Previous synthetic name" }, after: { ...after },
      before_sha256: "a".repeat(64), after_sha256: "b".repeat(64), created_at: "2026-09-18T18:00:00Z" };
  });
  return { resource_kind: kind as string, resource_id: id, items, next_cursor: null as string | null };
}
