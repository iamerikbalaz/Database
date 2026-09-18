import { resourceHistoryDto, historyResourceId } from "./resourceHistoryFixtures";
export const archiveMaterialId = historyResourceId;
export const archiveActorId = "55555555-5555-4555-8555-555555555555";
export function archiveDetailDto(version = 0, id = archiveMaterialId) {
  const material: Record<string, unknown> = { ...resourceHistoryDto("MATERIAL", 1, id).items[0].after, workflow_status: "IN_PROGRESS" };
  return { material,
    version, is_archived: version % 2 === 1, changed_at: version ? "2026-09-18T21:00:00Z" : null };
}
export function archivePreviewDto(version = 0, id = archiveMaterialId) {
  return { ...archiveDetailDto(version, id), action: version % 2 ? "RESTORE" : "ARCHIVE",
    input_sha256: "a".repeat(64), can_apply: true, blocked_code: null as string | null };
}
export function lifecycleEventDto(version = 1, id = archiveMaterialId) {
  return { id: `44444444-4444-4444-8444-${String(version).padStart(12, "0")}`, material_id: id,
    actor_id: archiveActorId, version, action: version % 2 ? "ARCHIVE" : "RESTORE",
    request_key: "33333333-3333-4333-8333-333333333333", request_sha256: "b".repeat(64), input_sha256: "a".repeat(64),
    reason: "Reviewed lifecycle decision", review_generation: version, created_at: "2026-09-18T21:00:00Z" };
}
