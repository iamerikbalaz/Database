import { request } from "./client";
import { nullable, record, string, uuid } from "./dto";
import { review } from "./reviewClient";

export type ApprovalKind = "TECHNICAL" | "PUBLICATION";
function number(value: unknown, maximum = Number.MAX_SAFE_INTEGER) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > maximum) throw new Error("Invalid technical report number");
  return value;
}
function hash(value: unknown) {
  const result = string(value); if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid technical report hash");
  return result;
}
function findings(value: unknown) {
  if (!Array.isArray(value) || value.length > 256) throw new Error("Invalid technical findings");
  return value.map((item) => { const data = record(item); const code = string(data.code);
    if (!/^[A-Z][A-Z0-9_]{0,99}$/.test(code)) throw new Error("Invalid finding code");
    return { code, path: string(data.path) };
  });
}
function parse(value: unknown) {
  const data = record(value); const state = review(data.review);
  const validation = data.validation === null ? null : (() => {
    const check = record(data.validation); const report = record(check.report);
    if (report.schema_version !== 1 || report.validator_version !== "pbr-images-1" || typeof report.can_approve !== "boolean" || !Array.isArray(report.images) || report.images.length > 64) throw new Error("Invalid technical report");
    const errors = findings(report.errors); const warnings = findings(report.warnings);
    if (report.can_approve !== (errors.length === 0) || check.generation !== state.generation || check.revision_hash !== state.revisionHash) throw new Error("Inconsistent technical review");
    return { id: uuid(check.id), actorId: uuid(check.actor_id), createdAt: string(check.created_at), reportHash: hash(check.report_hash),
      canApprove: report.can_approve, errors, warnings, images: report.images.map((value) => {
        const image = record(value);
        return { path: string(image.path), map: string(image.map), width: number(image.width, 32768), height: number(image.height, 32768), bits: number(image.bits, 16), format: string(image.format) };
      }) };
  })();
  if (!Array.isArray(data.approvals) || data.approvals.length > 2) throw new Error("Invalid approvals");
  const approvals = data.approvals.map((value) => {
    const item = record(value);
    if (!["TECHNICAL", "PUBLICATION"].includes(string(item.kind)) || item.generation !== state.generation || item.revision_hash !== state.revisionHash) throw new Error("Stale approval");
    return { id: uuid(item.id), kind: item.kind as ApprovalKind, actorId: uuid(item.actor_id), createdAt: string(item.created_at), note: nullable(item.note) };
  });
  return { review: state, validation, approvals };
}
export type TechnicalReview = ReturnType<typeof parse>;
export const technicalClient = {
  async current(id: string) { return parse(await request(`/materials/${uuid(id)}/technical-review`)); },
  async run(id: string, generation: number, key: string) {
    return parse(await request(`/materials/${uuid(id)}/technical-review/run`, "POST", { expected_generation: generation, idempotency_key: uuid(key) }));
  },
  async approve(id: string, current: TechnicalReview, kind: ApprovalKind, key: string, note: string, acknowledged: boolean) {
    if (!current.validation || !current.review.revisionHash) throw new Error("Technical report is required");
    return parse(await request(`/materials/${uuid(id)}/approvals`, "POST", { kind, expected_generation: current.review.generation,
      expected_revision_hash: current.review.revisionHash, technical_check_id: current.validation.id,
      idempotency_key: uuid(key), note: note.trim() || null, warnings_acknowledged: acknowledged }));
  },
};
