export const materialId = "10000000-0000-4000-8000-000000000001";
export const actorId = "10000000-0000-4000-8000-000000000002";
export const copyJob = { id: "10000000-0000-4000-8000-000000000003", proofSha256: "a".repeat(64),
  requestHash: "b".repeat(64), lastObservationId: "10000000-0000-4000-8000-000000000004" };
export const retirementId = "10000000-0000-4000-8000-000000000005";
export const dispatchId = "10000000-0000-4000-8000-000000000006";
export const reason = "Reviewed synthetic local copy";
export const createdAt = "2026-09-19T12:00:00Z";
export function retirementDto(status = "REMOVED") {
  return { id: retirementId, material_id: materialId, execution_id: copyJob.id,
    accepted_observation_id: copyJob.lastObservationId, proof_sha256: copyJob.proofSha256, actor_id: actorId,
    status, last_dispatch_id: status === "RESERVED" ? null : dispatchId, reason, created_at: createdAt,
    file_count: 2, byte_count: 1234, receipt: status === "REMOVED" ? {
      schema_version: 1, status: "REMOVED", operation_id: copyJob.id, request_hash: copyJob.requestHash,
      plan_hash: "c".repeat(64), proof_sha256: copyJob.proofSha256, retirement_id: retirementId,
      retirement_request_hash: "d".repeat(64), file_count: 2, byte_count: 1234,
    } : null };
}
export const retirementRequest = () => ({ idempotency_key: crypto.randomUUID(), expected_observation_id: copyJob.lastObservationId,
  expected_proof_sha256: copyJob.proofSha256, acknowledgement: "REMOVE_LOCAL_COPY" as const, reason });
export function removalAction(ordinal = 1) {
  return { id: dispatchId, ordinal, action: ordinal === 1 ? "EXECUTE" : "RECONCILE", actor_id: actorId, reason, created_at: createdAt,
    observation: { id: copyJob.lastObservationId, outcome: "REMOVED", actor_current: true, lease_current: true, failure_code: null as string | null, created_at: createdAt } };
}
