import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";
import type { PublicationBatch } from "./publicationClient";

const states = ["RESERVED", "RUNNING", "RECOVERY_REQUIRED", "STAGED_VERIFIED", "CLOSED"] as const;
export type StagingStatus = typeof states[number];
export type StagingAction = "run" | "reconcile" | "close" | "abandon";
export const stagingStatusLabel: Record<StagingStatus, string> = {
  RESERVED: "Reserved · ready to upload", RUNNING: "Transfer in progress", RECOVERY_REQUIRED: "Needs storage verification",
  STAGED_VERIFIED: "Storage verified", CLOSED: "Closed",
};
function text(value: unknown, max: number) { const result = string(value); if (!result || result.length > max) throw new Error("Invalid staging text"); return result; }
function hash(value: unknown) { const result = text(value, 64); if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid staging digest"); return result; }
function integer(value: unknown, min = 1, max = 2147483647) { if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) throw new Error("Invalid staging number"); return value; }
function timestamp(value: unknown) { const result = text(value, 64); if (!Number.isFinite(Date.parse(result))) throw new Error("Invalid staging timestamp"); return result; }
const nullableId = (value: unknown) => value === null ? null : uuid(value);
function list<T>(value: unknown, max: number, parse: (item: unknown) => T) { if (!Array.isArray(value) || value.length > max) throw new Error("Invalid staging list"); return value.map(parse); }
function unique(values: string[]) { if (new Set(values).size !== values.length) throw new Error("Duplicate staging reference"); }
function path(value: unknown) {
  const result = text(value, 900), parts = result.split("/");
  if (new TextEncoder().encode(result).length > 850 || parts.length > 16 || parts.some((part) => !part || part === "." || part === ".." || new TextEncoder().encode(part).length > 255) ||
      [...result].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127 || "\\:".includes(char))) throw new Error("Invalid staging object path");
  return result;
}
function target(value: Record<string, unknown>) {
  const bucketName = text(value.bucket_name, 63), stagingPrefix = text(value.staging_prefix, 128);
  if (!/^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$/.test(bucketName) || !/^[a-z0-9][a-z0-9_-]*(?:\/[a-z0-9][a-z0-9_-]*)*$/.test(stagingPrefix)) throw new Error("Invalid staging destination");
  return { bucketName, stagingPrefix };
}
function binding(value: unknown) {
  const item = record(value);
  return { materialId: uuid(item.material_id), executionId: uuid(item.execution_id), batchItemSha256: hash(item.batch_item_sha256),
    csvRowSha256: hash(item.csv_row_sha256), revisionHash: hash(item.revision_hash), contentContextHash: hash(item.content_context_hash),
    requestSha256: hash(item.worker_request_sha256), proofSha256: hash(item.packaging_proof_sha256) };
}
function materials(value: unknown) { const items = list(value, 100, binding); if (!items.length) throw new Error("Empty staging batch"); unique(items.map((item) => item.materialId)); unique(items.map((item) => item.executionId)); return items; }
function internal(value: Record<string, unknown>) { if (value.layout !== "INTERNAL_STAGING_V1" || value.importer_compatible !== false) throw new Error("Unknown staging/import contract"); }

export interface StagingSelection { material_id: string; execution_id: string; expected_observation_id: string; expected_proof_sha256: string }
export interface StagingPreviewRequest { job_id: string; expected_snapshot_hash: string; expected_csv_sha256: string; packages: StagingSelection[] }
export interface StagingReservation extends StagingPreviewRequest { batch_id: string; idempotency_key: string; expected_plan_sha256: string; reason: string }
export interface StagingCommand { idempotency_key: string; expected_plan_sha256: string; reason: string; expected_last_dispatch_id?: string | null; acknowledge_possible_remote_effects?: true }

export function stagingPreviewFromDto(value: unknown, batch: PublicationBatch, payload: StagingPreviewRequest) {
  const item = record(value); internal(item);
  const bound = materials(item.materials), objectCount = integer(item.object_count, 3, 20001), totalBytes = integer(item.total_bytes, 1, 256 * 1024 ** 3);
  if (uuid(item.job_id) !== payload.job_id || uuid(item.batch_id) !== batch.id || payload.expected_snapshot_hash !== batch.snapshotHash || payload.expected_csv_sha256 !== batch.csvSha256 || bound.length !== batch.rowCount || bound.length !== payload.packages.length) throw new Error("Wrong staging batch");
  for (const material of bound) {
    const selected = payload.packages.find((entry) => entry.material_id === material.materialId), row = batch.items.find((entry) => entry.materialId === material.materialId);
    if (!selected || !row || material.executionId !== selected.execution_id || material.proofSha256 !== selected.expected_proof_sha256 || material.batchItemSha256 !== row.snapshotHash || material.revisionHash !== row.row.revisionHash || material.contentContextHash !== row.row.contentContextHash) throw new Error("Wrong staging package selection");
  }
  const objects = list(item.objects_preview, 20, (value) => { const object = record(value); return { path: path(object.relative_path), size: integer(object.size, 1, 16 * 1024 ** 3), sha256: hash(object.sha256) }; });
  unique(objects.map((object) => object.path));
  if (objects.length !== Math.min(objectCount, 20) || boolean(item.has_more_objects) !== (objectCount > 20) || objects.reduce((sum, object) => sum + object.size, 0) > totalBytes) throw new Error("Invalid staging object preview");
  return { jobId: payload.job_id, batchId: batch.id, planSha256: hash(item.plan_sha256), ...target(item), materials: bound,
    objectCount, totalBytes, objects, transferEnabled: boolean(item.transfer_enabled) };
}
export type StagingPreview = ReturnType<typeof stagingPreviewFromDto>;

export function stagingSummaryFromDto(value: unknown) {
  const item = record(value), status = item.status as StagingStatus;
  if (!states.includes(status)) throw new Error("Invalid staging status");
  const closed = item.close === null ? null : record(item.close);
  const result = { id: uuid(item.id), batchId: uuid(item.batch_id), actorId: uuid(item.actor_id), status,
    planSha256: hash(item.plan_sha256), materialCount: integer(item.material_count, 1, 100), ...target(item),
    lastDispatchId: nullableId(item.last_dispatch_id), lastResultId: nullableId(item.last_result_id), createdAt: timestamp(item.created_at),
    close: closed && { id: uuid(closed.id), actorId: uuid(closed.actor_id), reason: text(closed.reason, 2000), dispatched: boolean(closed.dispatched), createdAt: timestamp(closed.created_at) } };
  if ((status === "CLOSED") !== (result.close !== null) || (status === "RESERVED" && (result.lastDispatchId || result.lastResultId)) ||
      (status === "RUNNING" && (!result.lastDispatchId || result.lastResultId)) || (["RECOVERY_REQUIRED", "STAGED_VERIFIED"].includes(status) && (!result.lastDispatchId || !result.lastResultId)) ||
      (result.lastResultId && !result.lastDispatchId) || (result.close && result.close.dispatched !== (result.lastDispatchId !== null))) throw new Error("Inconsistent staging history");
  return result;
}
export function stagingJobFromDto(value: unknown) {
  const item = record(value), summary = stagingSummaryFromDto(item); internal(item);
  const bound = materials(item.materials);
  if (bound.length !== summary.materialCount) throw new Error("Incomplete staging history");
  return { ...summary, reason: text(item.reason, 2000), materials: bound, objectCount: integer(item.object_count, 3, 20001), totalBytes: integer(item.total_bytes, 1, 256 * 1024 ** 3) };
}
export type StagingJob = ReturnType<typeof stagingJobFromDto>;
function boundJob(value: unknown, id: string) { const job = stagingJobFromDto(value); if (job.id !== id) throw new Error("Wrong staging job"); return job; }
function failure(value: unknown) { if (value === null) return null; const result = text(value, 64); if (!/^GCS_[A-Z0-9_]{1,60}$/.test(result)) throw new Error("Invalid staging failure code"); return result; }
function outcome(value: unknown) { if (value !== "VERIFIED" && value !== "UNCERTAIN") throw new Error("Invalid storage outcome"); return value; }
function resultFromDto(value: unknown) {
  if (value === null) return null;
  const item = record(value), result = { id: uuid(item.id), outcome: outcome(item.outcome), failureCode: failure(item.failure_code),
    completionId: nullableId(item.completion_observation_id), inputsCurrent: boolean(item.inputs_current), actorCurrent: boolean(item.actor_current),
    leaseCurrent: boolean(item.lease_current), createdAt: timestamp(item.created_at) };
  if ((result.outcome === "VERIFIED") !== (result.completionId !== null) || (result.outcome === "UNCERTAIN") !== (result.failureCode !== null)) throw new Error("Inconsistent storage result");
  return result;
}
export function stagingDispatchFromDto(value: unknown) {
  const item = record(value), action = text(item.action, 16), ordinal = integer(item.ordinal), previousId = nullableId(item.previous_dispatch_id);
  if ((ordinal === 1 && (action !== "EXECUTE" || previousId !== null)) || (ordinal > 1 && (action !== "RECONCILE" || previousId === null))) throw new Error("Invalid staging dispatch sequence");
  return { id: uuid(item.id), ordinal, previousId, action, actorId: uuid(item.actor_id), reason: text(item.reason, 2000),
    planSha256: hash(item.plan_sha256), createdAt: timestamp(item.created_at), result: resultFromDto(item.result) };
}
function generation(value: unknown) { const result = text(value, 19); if (!/^[1-9][0-9]{0,18}$/.test(result) || BigInt(result) > 9223372036854775807n) throw new Error("Invalid object generation"); return result; }
export function stagingTransferFromDto(value: unknown, job: StagingJob) {
  const item = record(value), relativePath = path(item.relative_path), size = integer(item.size, 1, 16 * 1024 ** 3), sha256 = hash(item.sha256);
  const ordinal = integer(item.ordinal, 1, job.objectCount + 1), marker = item.kind === "MARKER";
  if ((item.kind !== "DATA" && !marker) || marker !== (relativePath === "_reawote/complete.json") || marker !== (ordinal === job.objectCount + 1) || (marker && size > 32 * 1024 ** 2)) throw new Error("Invalid transfer intent");
  const raw = item.observation === null ? null : record(item.observation);
  let observation = null;
  if (raw) {
    const status = outcome(raw.outcome), failureCode = failure(raw.failure_code);
    let receipt = null;
    if (raw.receipt !== null) {
      const evidence = record(raw.receipt), spec = record(evidence.spec);
      if (Object.keys(evidence).length !== 5 || Object.keys(spec).length !== 5 || uuid(spec.job_id) !== job.id || hash(spec.binding_sha256) !== job.planSha256 ||
          path(spec.relative_path) !== relativePath || integer(spec.size, 1, 16 * 1024 ** 3) !== size || hash(spec.sha256) !== sha256 ||
          evidence.bucket_name !== job.bucketName || evidence.object_name !== `${job.stagingPrefix}/${job.id}/${relativePath}`) throw new Error("Unbound storage receipt");
      receipt = { generation: generation(evidence.generation), metageneration: generation(evidence.metageneration) };
    }
    if ((status === "VERIFIED") !== (receipt !== null) || (status === "UNCERTAIN") !== (failureCode !== null)) throw new Error("Inconsistent storage observation");
    observation = { id: uuid(raw.id), outcome: status, failureCode, receipt, createdAt: timestamp(raw.created_at) };
  }
  return { id: uuid(item.id), ordinal, kind: marker ? "MARKER" as const : "DATA" as const, path: relativePath, size, sha256, observation, createdAt: timestamp(item.created_at) };
}
function ordinalPage<T extends { id: string; ordinal: number }>(value: unknown, after: number | null, parse: (value: unknown) => T) {
  const page = record(value), items = list(page.items, 20, parse), nextCursor = page.next_cursor === null ? null : integer(page.next_cursor);
  unique(items.map((item) => item.id));
  if (items.some((item, index) => item.ordinal !== (after ?? 0) + index + 1) || (nextCursor !== null && (!items.length || items.at(-1)?.ordinal !== nextCursor))) throw new Error("Invalid staging cursor");
  return { items, nextCursor };
}
const root = "/publication-staging-jobs";
export const stagingClient = {
  async preview(batch: PublicationBatch, payload: StagingPreviewRequest) { return stagingPreviewFromDto(await request(`/publication-batches/${uuid(batch.id)}/staging-preview`, "POST", payload), batch, payload); },
  async reserve(payload: StagingReservation) {
    const result = boundJob(await request(root, "POST", payload), payload.job_id);
    if (result.batchId !== payload.batch_id || result.planSha256 !== payload.expected_plan_sha256 || result.reason !== payload.reason || result.materials.length !== payload.packages.length || result.materials.some((item) => !payload.packages.some((selected) => selected.material_id === item.materialId && selected.execution_id === item.executionId && selected.expected_proof_sha256 === item.proofSha256))) throw new Error("Wrong staging reservation");
    return result;
  },
  async detail(id: string) { return boundJob(await request(`${root}/${uuid(id)}`), id); },
  async history(after: string | null = null) {
    const page = record(await request(root + (after ? `?after=${uuid(after)}` : ""))), items = list(page.items, 20, stagingSummaryFromDto), nextCursor = nullableId(page.next_cursor);
    unique(items.map((item) => item.id));
    if (nextCursor && (!items.length || items.at(-1)?.id !== nextCursor || nextCursor === after)) throw new Error("Invalid staging history cursor");
    return { enabled: boolean(page.enabled), items, nextCursor };
  },
  async command(job: StagingJob, action: StagingAction, payload: StagingCommand) {
    if (!["run", "reconcile", "close", "abandon"].includes(action) || payload.expected_plan_sha256 !== job.planSha256) throw new Error("Invalid staging command");
    const result = boundJob(await request(`${root}/${uuid(job.id)}/${action}`, "POST", payload), job.id);
    if (result.planSha256 !== job.planSha256 || result.batchId !== job.batchId) throw new Error("Staging history changed");
    return result;
  },
  async dispatches(job: StagingJob, after: number | null = null) {
    const page = ordinalPage(await request(`${root}/${uuid(job.id)}/dispatches${after === null ? "" : `?after=${integer(after)}`}`), after, stagingDispatchFromDto);
    if (page.items.some((item) => item.planSha256 !== job.planSha256)) throw new Error("Wrong dispatch plan");
    return page;
  },
  async transfers(job: StagingJob, dispatchId: string, after: number | null = null) {
    return ordinalPage(await request(`${root}/${uuid(job.id)}/dispatches/${uuid(dispatchId)}/transfers${after === null ? "" : `?after=${integer(after, 1, 20002)}`}`), after, (value) => stagingTransferFromDto(value, job));
  },
};
