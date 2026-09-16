import { request } from "./client";
import { contentFromDto } from "./catalogClient";
import { boolean, nullable, record, string, uuid } from "./dto";

function hash(input: unknown) {
  const value = string(input);
  if (!/^[a-f0-9]{64}$/.test(value)) throw new Error("Invalid content context");
  return value;
}
function revision(input: unknown) {
  if (typeof input !== "number" || !Number.isSafeInteger(input) || input < 0) throw new Error("Invalid content revision");
  return input;
}
function codes(input: unknown) {
  if (!Array.isArray(input) || input.length > 100) throw new Error("Invalid content findings");
  return input.map(string);
}
function approvalFromDto(input: unknown) {
  const value = record(input);
  return { id: uuid(value.id), actorId: uuid(value.actor_id), revision: revision(value.content_revision),
    contextHash: hash(value.context_hash), note: nullable(value.note), warningsAcknowledged: boolean(value.warnings_acknowledged), createdAt: string(value.created_at) };
}
function snapshotFromDto(input: unknown) {
  const value = record(input), brand = record(value.brand), material = record(value.material), source = record(value.source_review);
  if (value.schema_version !== 1) throw new Error("Unknown content review schema");
  return { content: contentFromDto(value.content), brandName: string(brand.name), brandIdentifier: string(brand.brand_identifier),
    materialName: string(material.material_name), identity: string(material.technical_identity),
    sourceGeneration: revision(source.generation), sourceHash: source.revision_hash === null ? null : hash(source.revision_hash) };
}
export function contentReviewFromDto(input: unknown) {
  const value = record(input), snapshot = snapshotFromDto(value.snapshot);
  const materialId = uuid(value.material_id), contentRevision = revision(value.content_revision), contextHash = hash(value.context_hash);
  const approval = value.approval === null ? null : approvalFromDto(value.approval);
  const errors = codes(value.errors), warnings = codes(value.warnings), canApprove = boolean(value.can_approve);
  if (snapshot.content.materialId !== materialId || snapshot.content.revision !== contentRevision ||
      (approval && (approval.contextHash !== contextHash || approval.revision !== contentRevision)) ||
      (canApprove && (errors.length > 0 || approval !== null || contentRevision === 0)) ||
      value.content_status !== (approval ? "APPROVED" : snapshot.content.status)) throw new Error("Inconsistent content review");
  return { materialId, revision: contentRevision, contextHash, snapshot, approval, errors, warnings, canApprove };
}
export type ContentReview = ReturnType<typeof contentReviewFromDto>;
export interface ContentApprovalPayload {
  idempotency_key: string; expected_revision: number; expected_context_hash: string; warnings_acknowledged: boolean; note: string | null;
}
export const contentReviewClient = {
  async review(id: string) { return contentReviewFromDto(await request(`/materials/${uuid(id)}/content-review`)); },
  async approve(id: string, payload: ContentApprovalPayload) { return contentReviewFromDto(await request(`/materials/${uuid(id)}/content/approve`, "POST", payload)); },
  async history(id: string) {
    const value = await request(`/materials/${uuid(id)}/content-approvals`);
    if (!Array.isArray(value) || value.length > 100) throw new Error("Invalid content approval history");
    return value.map((item) => ({ ...approvalFromDto(item), snapshot: snapshotFromDto(record(item).snapshot) }));
  },
};
