import { request } from "./client";
import { boolean, nullable, record, string, uuid } from "./dto";

function hash(value: unknown) {
  const result = string(value);
  if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid identity hash");
  return result;
}
function number(value: unknown) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error("Invalid identity number");
  return value;
}
function list<T>(value: unknown, limit: number, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length > limit) throw new Error("Invalid identity list");
  return value.map(parse);
}
function context(value: unknown) {
  const data = record(value);
  return { materialId: uuid(data.material_id), identity: string(data.technical_identity), folder: nullable(data.folder_path),
    brandId: uuid(data.published_brand_id), category: string(data.main_category_code), number: number(data.sequence_number),
    name: string(data.material_name) };
}
function finding(value: unknown) {
  const item = record(value);
  const code = string(item.code);
  if (!/^[A-Z][A-Z0-9_]{0,99}$/.test(code)) throw new Error("Invalid identity finding");
  return { code, path: string(item.path) };
}
function plan(value: unknown) {
  const data = record(value); const worker = record(data.worker_plan); const metadata = record(worker.metadata);
  if (worker.schema_version !== 1 || worker.planner_version !== "identity-plan-1") throw new Error("Unknown identity planner");
  const errors = list(worker.errors, 40010, finding);
  const ready = boolean(worker.ready);
  if (ready !== (errors.length === 0)) throw new Error("Inconsistent identity plan");
  const source = context(data.source_context); const target = context(data.target_context);
  if (source.materialId !== target.materialId || source.folder !== worker.source_path || target.folder !== worker.target_path) throw new Error("Mismatched identity plan");
  return { source, target, generation: number(data.generation), hash: hash(data.proposal_hash), reservesNumber: boolean(data.reserves_number),
    workerHash: hash(worker.plan_hash), sourceHash: hash(worker.source_revision_hash), ready, errors,
    warnings: list(worker.warnings, 10, finding),
    metadata: { beforeHash: metadata.before_hash === null ? null : hash(metadata.before_hash), afterHash: metadata.after_hash === null ? null : hash(metadata.after_hash),
      fields: list(metadata.changed_fields, 9, string) },
    changes: list(worker.changes, 20000, (value) => { const item = record(value);
      if (item.kind !== "file" && item.kind !== "directory") throw new Error("Unknown rename kind");
      return { source: string(item.source), target: string(item.target), kind: item.kind,
        hash: item.sha256 === null ? null : hash(item.sha256) };
    }),
  };
}
export type IdentityPlan = ReturnType<typeof plan>;
export interface IdentityTarget { target_brand_id: string; main_category_code: string; target_parent: string; }
export interface IdentityConfirmation extends IdentityTarget {
  idempotency_key: string; expected_generation: number; expected_proposal_hash: string; reason: string; warnings_acknowledged: boolean;
}
function operation(value: unknown) {
  const data = record(value); const status = string(data.status);
  if (!["RUNNING", "COMPLETED", "ROLLED_BACK", "RECOVERY_REQUIRED", "REJECTED"].includes(status)) throw new Error("Unknown identity outcome");
  const result = data.result === null ? null : record(data.result);
  return { id: uuid(data.id), status, source: context(data.source_context), target: context(data.target_context),
    reason: string(data.reason), createdAt: string(data.created_at), actorId: uuid(data.actor_id),
    failure: result ? nullable(result.failure_code) : null };
}
export type IdentityOperation = ReturnType<typeof operation>;
export const identityClient = {
  async operations(id: string) {
    const data = record(await request(`/materials/${uuid(id)}/identity-operations`));
    return { enabled: boolean(data.mutations_enabled), items: list(data.operations, 100, operation) };
  },
  async plan(id: string, target: IdentityTarget) { return plan(await request(`/materials/${uuid(id)}/identity-plan`, "POST", target)); },
  async confirm(id: string, payload: IdentityConfirmation) { return operation(await request(`/materials/${uuid(id)}/identity-confirm`, "POST", payload)); },
  async resume(id: string, operationId: string) { return operation(await request(`/materials/${uuid(id)}/identity-operations/${uuid(operationId)}/resume`, "POST")); },
};
