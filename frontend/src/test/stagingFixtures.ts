import { publicationBatchDto, publicationId } from "./publicationFixtures";
import { materialDto, processorDto } from "./materialFixtures";
import type { StagingPreviewRequest } from "../api/stagingClient";

export const stagingId = "80000000-0000-4000-8000-000000000001", stagingDispatchId = "80000000-0000-4000-8000-000000000002";
export const packageId = "80000000-0000-4000-8000-000000000003", observationId = "80000000-0000-4000-8000-000000000004";
export const stagingHash = "f".repeat(64), proofHash = "9".repeat(64), date = "2026-09-18T12:00:00Z";
export const selection = (): StagingPreviewRequest => ({ job_id: stagingId, expected_snapshot_hash: publicationBatchDto().snapshot_hash,
  expected_csv_sha256: publicationBatchDto().csv_sha256, packages: [{ material_id: materialDto.id, execution_id: packageId,
    expected_observation_id: observationId, expected_proof_sha256: proofHash }] });
function binding() { return { material_id: materialDto.id, execution_id: packageId, batch_item_sha256: "d".repeat(64), csv_row_sha256: "1".repeat(64),
  revision_hash: "a".repeat(64), content_context_hash: "b".repeat(64), worker_request_sha256: "2".repeat(64), packaging_proof_sha256: proofHash }; }
export function stagingPreviewDto(id = stagingId, enabled = true) { return { job_id: id, batch_id: publicationId, plan_sha256: stagingHash,
  layout: "INTERNAL_STAGING_V1", transfer_enabled: enabled, importer_compatible: false, bucket_name: "synthetic-reawote-staging", staging_prefix: "isolated/contracts",
  object_count: 3, total_bytes: 300, materials: [binding()], objects_preview: [
    { relative_path: `materials/${materialDto.id}/material.zip`, size: 100, sha256: "3".repeat(64), material_id: materialDto.id, source_path: "material.zip" },
    { relative_path: `materials/${materialDto.id}/metadata.json`, size: 100, sha256: "4".repeat(64), material_id: materialDto.id, source_path: "metadata.json" },
    { relative_path: "publication.csv", size: 100, sha256: "5".repeat(64), material_id: null, source_path: null },
  ], has_more_objects: false }; }
export function stagingJobDto(status = "RESERVED", dispatched = status !== "RESERVED" && status !== "CLOSED") {
  const preview = stagingPreviewDto();
  return { id: stagingId, batch_id: publicationId, actor_id: processorDto.id, status, plan_sha256: stagingHash, material_count: 1,
    bucket_name: preview.bucket_name, staging_prefix: preview.staging_prefix, last_dispatch_id: dispatched ? stagingDispatchId : null,
    last_result_id: dispatched && status !== "RUNNING" ? observationId : null, created_at: date,
    close: status === "CLOSED" ? { id: observationId, actor_id: processorDto.id, reason: "Close synthetic staging", dispatched, created_at: date } : null,
    reason: "Reviewed synthetic staging", layout: preview.layout, importer_compatible: false, object_count: preview.object_count, total_bytes: preview.total_bytes, materials: preview.materials };
}
export function packagedDto() { return { id: packageId, material_id: materialDto.id, batch_id: publicationId, actor_id: processorDto.id, policy_id: stagingId,
  status: "PACKAGED", input_hash: "d".repeat(64), worker_request_hash: "2".repeat(64), last_dispatch_id: stagingDispatchId, last_observation_id: observationId,
  proof_sha256: proofHash, failure_code: null, inputs_current: true, actor_current: true, terminal: "OPEN", created_at: date, updated_at: date }; }
export function stagingDispatchDto() { return { id: stagingDispatchId, ordinal: 1, previous_dispatch_id: null, action: "EXECUTE", actor_id: processorDto.id,
  reason: "Synthetic transfer", plan_sha256: stagingHash, created_at: date, result: { id: observationId, outcome: "VERIFIED", failure_code: null,
    completion_observation_id: stagingId, inputs_current: true, actor_current: true, lease_current: true, created_at: date } }; }
export function stagingTransferDto() {
  const object = stagingPreviewDto().objects_preview[0];
  return { id: packageId, ordinal: 1, kind: "DATA", relative_path: object.relative_path, size: object.size, sha256: object.sha256, created_at: date,
    observation: { id: observationId, outcome: "VERIFIED", failure_code: null, created_at: date,
      receipt: { spec: { job_id: stagingId, binding_sha256: stagingHash, relative_path: object.relative_path, size: object.size, sha256: object.sha256 },
        bucket_name: "synthetic-reawote-staging", object_name: `isolated/contracts/${stagingId}/${object.relative_path}`, generation: "9007199254740993", metageneration: "1" } } };
}
