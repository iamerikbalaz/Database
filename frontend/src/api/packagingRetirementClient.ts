import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";
import type { PackagingJob } from "./packagingClient";

export type CopyJob = Pick<PackagingJob, "id" | "proofSha256" | "lastObservationId" | "requestHash">;
const statuses = ["RESERVED", "RUNNING", "RECOVERY_REQUIRED", "REMOVED"] as const;
function hash(value: unknown) { const result = string(value); if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid retirement digest"); return result; }
function text(value: unknown, max: number) { const result = string(value); if (!result.trim() || result.length > max) throw new Error("Invalid retirement text"); return result; }
function integer(value: unknown, minimum: number, maximum: number) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > maximum) throw new Error("Invalid retirement count"); return value;
}
const nullableId = (value: unknown) => value === null ? null : uuid(value);
const path = (materialId: string, job: CopyJob) => `/materials/${uuid(materialId)}/packaging-executions/${uuid(job.id)}/retirement`;

export function retirementFromDto(value: unknown, materialId: string, job: CopyJob) {
  const item = record(value), status = string(item.status);
  if (!statuses.includes(status as typeof statuses[number]) || uuid(item.material_id) !== materialId || uuid(item.execution_id) !== job.id ||
      uuid(item.accepted_observation_id) !== job.lastObservationId || hash(item.proof_sha256) !== job.proofSha256) throw new Error("Wrong retired copy");
  const result = { id: uuid(item.id), materialId, executionId: job.id, proofSha256: hash(item.proof_sha256),
    actorId: uuid(item.actor_id), reason: text(item.reason, 2000), createdAt: text(item.created_at, 64),
    status: status as typeof statuses[number], lastDispatchId: nullableId(item.last_dispatch_id),
    fileCount: integer(item.file_count, 2, 20008), byteCount: integer(item.byte_count, 0, 128 * 1024 ** 3) };
  if ((result.status === "RESERVED") !== (result.lastDispatchId === null)) throw new Error("Invalid retirement progress");
  if (result.status === "REMOVED") {
    const receipt = record(item.receipt);
    if (Object.keys(receipt).sort().join(",") !== ["schema_version", "status", "operation_id", "request_hash", "plan_hash", "proof_sha256", "retirement_id", "retirement_request_hash", "file_count", "byte_count"].sort().join(",") ||
        receipt.schema_version !== 1 || receipt.status !== "REMOVED" || uuid(receipt.operation_id) !== job.id ||
        hash(receipt.request_hash) !== job.requestHash || hash(receipt.proof_sha256) !== result.proofSha256 ||
        uuid(receipt.retirement_id) !== result.id || receipt.file_count !== result.fileCount || receipt.byte_count !== result.byteCount) throw new Error("Invalid removal receipt");
    hash(receipt.plan_hash); hash(receipt.retirement_request_hash);
  } else if (item.receipt !== null) throw new Error("Unexpected removal receipt");
  return result;
}
export type Retirement = ReturnType<typeof retirementFromDto>;
export interface RetirementRequest {
  idempotency_key: string; expected_observation_id: string; expected_proof_sha256: string;
  acknowledgement: "REMOVE_LOCAL_COPY"; reason: string;
}
export interface RetirementRecovery {
  idempotency_key: string; expected_retirement_id: string; expected_proof_sha256: string;
  expected_last_dispatch_id: string | null; acknowledgement: "REMOVE_LOCAL_COPY"; reason: string;
}
function validate(body: RetirementRequest | RetirementRecovery, job: CopyJob) {
  uuid(body.idempotency_key); text(body.reason, 2000);
  if (body.acknowledgement !== "REMOVE_LOCAL_COPY" || hash(body.expected_proof_sha256) !== job.proofSha256) throw new Error("Wrong removal acknowledgement");
}
function dispatchFromDto(value: unknown) {
  const item = record(value), action = string(item.action), ordinal = integer(item.ordinal, 1, 2147483647);
  if ((ordinal === 1 && action !== "EXECUTE") || (ordinal > 1 && action !== "RECONCILE")) throw new Error("Invalid retirement action");
  const observed = item.observation === null ? null : record(item.observation);
  const failure = observed?.failure_code;
  if (observed && ((observed.outcome !== "REMOVED" && observed.outcome !== "UNCERTAIN") ||
      (observed.outcome === "REMOVED" && failure !== null) ||
      (observed.outcome === "UNCERTAIN" && (typeof failure !== "string" || !/^PACKAGING_[A-Z0-9_]{1,90}$/.test(failure))))) throw new Error("Invalid retirement observation");
  return { id: uuid(item.id), ordinal, action, actorId: uuid(item.actor_id), reason: text(item.reason, 2000), createdAt: text(item.created_at, 64),
    observation: observed && { id: uuid(observed.id), outcome: string(observed.outcome), actorCurrent: boolean(observed.actor_current),
      leaseCurrent: boolean(observed.lease_current), failureCode: failure === null ? null : string(failure), createdAt: text(observed.created_at, 64) } };
}
export const packagingRetirementClient = {
  async read(materialId: string, job: CopyJob) {
    const page = record(await request(path(materialId, job)));
    return { enabled: boolean(page.enabled), retirement: page.retirement === null ? null : retirementFromDto(page.retirement, materialId, job) };
  },
  async retire(materialId: string, job: CopyJob, actorId: string, body: RetirementRequest) {
    validate(body, job);
    if (uuid(body.expected_observation_id) !== job.lastObservationId) throw new Error("Wrong accepted observation");
    const result = retirementFromDto(await request(path(materialId, job), "POST", body), materialId, job);
    if (result.actorId !== uuid(actorId) || result.reason !== body.reason) throw new Error("Wrong retirement intent");
    return result;
  },
  async recover(materialId: string, job: CopyJob, body: RetirementRecovery) {
    validate(body, job); uuid(body.expected_retirement_id); nullableId(body.expected_last_dispatch_id);
    const result = retirementFromDto(await request(path(materialId, job) + "/reconcile", "POST", body), materialId, job);
    if (result.id !== body.expected_retirement_id) throw new Error("Wrong recovered retirement");
    return result;
  },
  async history(materialId: string, job: CopyJob, after: number | null = null) {
    const offset = after === null ? 0 : integer(after, 1, 2147483647);
    const page = record(await request(path(materialId, job) + `/dispatches?after=${offset}`));
    if (!Array.isArray(page.items) || page.items.length > 20) throw new Error("Invalid retirement history");
    const items = page.items.map(dispatchFromDto), nextCursor = page.next_cursor === null ? null : integer(page.next_cursor, 1, 2147483647);
    if (new Set(items.map((item) => item.id)).size !== items.length || items.some((item, index) => item.ordinal <= (index ? items[index - 1].ordinal : offset)) ||
        (nextCursor !== null && nextCursor !== items.at(-1)?.ordinal)) throw new Error("Invalid retirement history cursor");
    return { items, nextCursor };
  },
};
