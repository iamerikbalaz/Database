import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";
import { apiUrl } from "../auth/sessionTransport";

const statuses = ["RESERVED", "RUNNING", "RETRY_REQUIRED", "RECOVERY_REQUIRED", "PACKAGED", "REJECTED"] as const;
export type PackagingStatus = typeof statuses[number];
export type PackagingAction = "run" | "retry" | "reconcile" | "close";
export const packagingStatusLabel: Record<PackagingStatus, string> = {
  RESERVED: "Reserved · ready to start", RUNNING: "Request in progress", RETRY_REQUIRED: "Incomplete · retry available",
  RECOVERY_REQUIRED: "Needs reconciliation", PACKAGED: "Packaged", REJECTED: "Closed",
};
const nullableId = (value: unknown) => value === null ? null : uuid(value);
const nullableBool = (value: unknown) => value === null ? null : boolean(value);
function hash(value: unknown) { const text = string(value); if (!/^[a-f0-9]{64}$/.test(text)) throw new Error("Invalid packaging digest"); return text; }
function integer(value: unknown) { if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1 || value > 2147483647) throw new Error("Invalid packaging ordinal"); return value; }
function text(value: unknown, max: number) { const result = string(value); if (!result || result.length > max) throw new Error("Invalid packaging text"); return result; }
function terminal(value: unknown) { if (value !== null && value !== "OPEN" && value !== "CLOSING" && value !== "CLOSED") throw new Error("Invalid packaging closure"); return value; }
function failure(value: unknown) { if (value === null) return null; const result = string(value); if (!/^PACKAGING_[A-Z0-9_]{1,90}$/.test(result)) throw new Error("Invalid packaging code"); return result; }
export function packagingJobFromDto(value: unknown, materialId: string) {
  const item = record(value), status = item.status as PackagingStatus;
  if (uuid(item.material_id) !== materialId || !statuses.includes(status)) throw new Error("Wrong packaging material or state");
  const result = { id: uuid(item.id), materialId, batchId: uuid(item.batch_id), actorId: uuid(item.actor_id), policyId: uuid(item.policy_id), status,
    inputHash: hash(item.input_hash), requestHash: hash(item.worker_request_hash), lastDispatchId: nullableId(item.last_dispatch_id),
    lastObservationId: nullableId(item.last_observation_id), proofSha256: item.proof_sha256 === null ? null : hash(item.proof_sha256),
    failureCode: failure(item.failure_code), inputsCurrent: nullableBool(item.inputs_current), actorCurrent: nullableBool(item.actor_current),
    terminal: terminal(item.terminal), createdAt: text(item.created_at, 64), updatedAt: text(item.updated_at, 64) };
  if ((status === "RESERVED") !== (result.lastDispatchId === null) ||
      (["RESERVED", "RUNNING"].includes(status) !== (result.lastObservationId === null)) ||
      (result.lastObservationId === null && [result.proofSha256, result.failureCode, result.inputsCurrent, result.actorCurrent, result.terminal].some((value) => value !== null)) ||
      (status === "PACKAGED" && (!result.proofSha256 || !result.inputsCurrent || !result.actorCurrent || result.terminal !== "OPEN")) ||
      (status === "RETRY_REQUIRED" && (result.proofSha256 !== null || result.terminal !== "OPEN"))) throw new Error("Inconsistent packaging progress");
  return result;
}
export type PackagingJob = ReturnType<typeof packagingJobFromDto>;
export interface PackagingReservation {
  idempotency_key: string; batch_id: string; expected_snapshot_hash: string; expected_policy_id: string; reason: string;
}
export interface PackagingCommand { idempotency_key: string; expected_last_dispatch_id: string | null; reason: string }
const path = (materialId: string) => `/materials/${uuid(materialId)}/packaging-executions`;
function bound(value: unknown, materialId: string, id: string) {
  const result = packagingJobFromDto(value, materialId); if (result.id !== id) throw new Error("Wrong packaging execution"); return result;
}
function dispatchFromDto(value: unknown) {
  const item = record(value), action = text(item.action, 20);
  if (!["EXECUTE", "RETRY", "RECONCILE", "CLOSE"].includes(action)) throw new Error("Invalid packaging action");
  const observed = item.observation === null ? null : record(item.observation);
  if (observed && !["READY", "RETRY_REQUIRED", "UNCERTAIN", "NOT_STARTED"].includes(string(observed.outcome))) throw new Error("Invalid packaging outcome");
  return { id: uuid(item.id), ordinal: integer(item.ordinal), action, actorId: uuid(item.actor_id), reason: text(item.reason, 2000), createdAt: text(item.created_at, 64),
    observation: observed && { id: uuid(observed.id), outcome: string(observed.outcome), inputsCurrent: boolean(observed.inputs_current), actorCurrent: boolean(observed.actor_current),
      proofSha256: observed.proof_sha256 === null ? null : hash(observed.proof_sha256), failureCode: failure(observed.failure_code), terminal: terminal(observed.terminal),
      attempt: observed.attempt === null ? null : integer(observed.attempt), createdAt: text(observed.created_at, 64) } };
}
export const packagingClient = {
  async files(materialId: string, job: Pick<PackagingJob, "id" | "proofSha256">, after: number | null = null) {
    const expected = hash(job.proofSha256), offset = after === null ? 0 : integer(after);
    if (offset > 20008) throw new Error("Invalid artifact cursor");
    const page = record(await request(`${path(materialId)}/${uuid(job.id)}/artifacts${after === null ? "" : `?after=${offset}`}`));
    if (uuid(page.execution_id) !== job.id || hash(page.proof_sha256) !== expected || !Array.isArray(page.items) || page.items.length > 20) throw new Error("Wrong packaged files");
    const items = await Promise.all(page.items.map(async (value) => {
      const item = record(value), name = text(item.path, 2048), parts = name.split("/"), id = hash(item.id);
      if (new TextEncoder().encode(name).length > 2048 || parts.length > 16 || parts.some((part) => !part || part === "." || part === ".." || new TextEncoder().encode(part).length > 255 ||
          [...part].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127 || "\\:".includes(char))) ||
          !(name === "metadata.json" || (parts.length === 1 && name.endsWith(".zip")) || name.startsWith("PREVIEW/")) ||
          typeof item.size !== "number" || !Number.isSafeInteger(item.size) || item.size < 0 || item.size > 16 * 1024 ** 3) throw new Error("Invalid packaged file");
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(name))), (byte) => byte.toString(16).padStart(2, "0")).join("");
      if (digest !== id) throw new Error("Wrong packaged file identity");
      return { id, path: name, size: item.size, sha256: hash(item.sha256) };
    }));
    const nextCursor = page.next_cursor === null ? null : integer(page.next_cursor);
    if (new Set(items.map((item) => item.id)).size !== items.length || (nextCursor !== null && (!items.length || nextCursor !== offset + items.length || nextCursor > 20008))) throw new Error("Invalid artifact page");
    return { items, nextCursor };
  },
  artifactUrl(materialId: string, job: Pick<PackagingJob, "id" | "proofSha256">, fileId: string) {
    return apiUrl(`${path(materialId)}/${uuid(job.id)}/artifacts/${hash(fileId)}?proof_sha256=${hash(job.proofSha256)}`);
  },
  async history(materialId: string, after: string | null = null) {
    const page = record(await request(path(materialId) + (after ? `?after=${uuid(after)}` : "")));
    if (!Array.isArray(page.items) || page.items.length > 20) throw new Error("Invalid packaging history");
    const items = page.items.map((item) => packagingJobFromDto(item, materialId)), nextCursor = nullableId(page.next_cursor);
    if (new Set(items.map((item) => item.id)).size !== items.length || (nextCursor && (items.at(-1)?.id !== nextCursor || nextCursor === after))) throw new Error("Invalid packaging cursor");
    return { enabled: boolean(page.enabled), items, nextCursor };
  },
  async detail(materialId: string, id: string) { return bound(await request(`${path(materialId)}/${uuid(id)}`), materialId, id); },
  async reserve(materialId: string, body: PackagingReservation) {
    const value = record(await request(path(materialId), "POST", body)), result = packagingJobFromDto(value, materialId);
    if (result.batchId !== body.batch_id || result.inputHash !== body.expected_snapshot_hash || result.policyId !== body.expected_policy_id || value.reason !== body.reason) throw new Error("Mismatched packaging reservation");
    return result;
  },
  async command(materialId: string, id: string, action: PackagingAction, body: PackagingCommand) {
    if (!["run", "retry", "reconcile", "close"].includes(action)) throw new Error("Invalid packaging action");
    return bound(await request(`${path(materialId)}/${uuid(id)}/${action}`, "POST", body), materialId, id);
  },
  async dispatches(materialId: string, id: string, after: number | null = null) {
    const page = record(await request(`${path(materialId)}/${uuid(id)}/dispatches${after ? `?after=${integer(after)}` : ""}`));
    if (!Array.isArray(page.items) || page.items.length > 20) throw new Error("Invalid dispatch history");
    const items = page.items.map(dispatchFromDto), nextCursor = page.next_cursor === null ? null : integer(page.next_cursor);
    if (new Set(items.map((item) => item.id)).size !== items.length || items.some((item, index) => item.ordinal <= (index ? items[index - 1].ordinal : after ?? 0)) ||
        (nextCursor !== null && nextCursor !== items.at(-1)?.ordinal)) throw new Error("Invalid dispatch cursor");
    return { items, nextCursor };
  },
};
