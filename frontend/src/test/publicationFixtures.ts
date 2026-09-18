import { materialDto, processorDto } from "./materialFixtures";
export const publicationId = "90000000-0000-4000-8000-000000000001";
export function publicationRowDto() { return { material_id: materialDto.id, revision_hash: "a".repeat(64), content_context_hash: "b".repeat(64),
  identity_name: materialDto.technical_identity, name: materialDto.material_name, description: "Reviewed synthetic surface", credits: 12,
  width_cm: "12.5", height_cm: "34", brand_identifier: "synthetic-brand", categories: ["Stone"], color: "#A1B2C3", tags: ["matte"] }; }
export function publicationPreviewDto() { return { can_prepare: true, preview_hash: "c".repeat(64), items: [{ material_id: materialDto.id,
  name: materialDto.material_name, identity_name: materialDto.technical_identity, snapshot_hash: "d".repeat(64), revision_hash: "a".repeat(64),
  content_context_hash: "b".repeat(64), row: publicationRowDto(), errors: [] as string[], warnings: [] as { material_id: string; code: string; fields: string[] }[] }] }; }
export function publicationBatchDto() { return { id: publicationId, actor_id: processorDto.id, status: "PREPARED", row_count: 1, snapshot_hash: "c".repeat(64),
  csv_sha256: "e".repeat(64), created_at: "2026-09-17T12:00:00Z", reason: "Prepare synthetic batch", warnings_acknowledged: false,
  warnings: [] as { material_id: string; code: string; fields: string[] }[], items: [{ material_id: materialDto.id, ordinal: 1, snapshot_hash: "d".repeat(64),
    row: publicationRowDto(), technical_approval_id: publicationId, publication_approval_id: publicationId, content_approval_id: publicationId, metadata_snapshot_id: publicationId }] }; }
