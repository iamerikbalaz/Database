import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";
import { responseError } from "./errors";
import { apiUrl, notifySessionInvalidation, sessionGeneration } from "../auth/sessionTransport";

function text(value: unknown, max: number) { const result = string(value); if (result.length > max) throw new Error("Invalid publication text"); return result; }
function hash(value: unknown) { const result = string(value); if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid publication digest"); return result; }
function integer(value: unknown, min: number, max: number) { if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) throw new Error("Invalid publication number"); return value; }
function list<T>(value: unknown, max: number, parse: (item: unknown) => T): T[] { if (!Array.isArray(value) || value.length > max) throw new Error("Invalid publication list"); return value.map(parse); }
function unique(values: string[]) { if (new Set(values).size !== values.length) throw new Error("Duplicate publication reference"); }
function code(value: unknown) { const result = string(value); if (!/^[A-Z][A-Z0-9_]{0,99}$/.test(result)) throw new Error("Invalid publication code"); return result; }
function dimension(value: unknown) { const result = text(value, 20); if (!/^\d{1,8}(\.\d{1,4})?$/.test(result) || Number(result) <= 0) throw new Error("Invalid publication dimension"); return result; }
const columns = ["identity_name", "name", "description", "credits", "dimension", "brand_identifier", "categories", "color", "tags"];
function row(value: unknown, materialId: string) {
  const item = record(value), color = string(item.color);
  if (uuid(item.material_id) !== materialId || !/^#[A-F0-9]{6}$/.test(color)) throw new Error("Invalid publication row");
  const categories = list(item.categories, 100, (value) => text(value, 255));
  if (!categories.length) throw new Error("Missing publication category");
  return { materialId, revisionHash: hash(item.revision_hash), contentContextHash: hash(item.content_context_hash),
    identityName: text(item.identity_name, 512), name: text(item.name, 255), description: item.description === null ? null : text(item.description, 10000),
    credits: integer(item.credits, 0, 2147483647), widthCm: dimension(item.width_cm), heightCm: dimension(item.height_cm),
    brandIdentifier: text(item.brand_identifier, 255), categories, color, tags: list(item.tags, 100, (value) => text(value, 100)) };
}
export type PublicationRow = ReturnType<typeof row>;
function warning(value: unknown) {
  const item = record(value), fields = list(item.fields, 9, string), warningCode = code(item.code);
  if (fields.some((field) => !columns.includes(field)) || !["CONTENT_DESCRIPTION_EMPTY", "CONTENT_TAGS_EMPTY", "CSV_FORMULA_LIKE_VALUE"].includes(warningCode)) throw new Error("Invalid publication warning");
  return { materialId: uuid(item.material_id), code: warningCode, fields };
}
export type PublicationWarning = ReturnType<typeof warning>;
export function publicationPreviewFromDto(value: unknown, selected: string[]) {
  const result = record(value), expected = selected.map(uuid); unique(expected);
  const items = list(result.items, 100, (value) => {
    const item = record(value), materialId = uuid(item.material_id), parsedRow = item.row === null ? null : row(item.row, materialId);
    const revisionHash = item.revision_hash === null ? null : hash(item.revision_hash), contentContextHash = hash(item.content_context_hash);
    const warnings = list(item.warnings, 3, warning), name = text(item.name, 255), identityName = text(item.identity_name, 512);
    if (warnings.some((entry) => entry.materialId !== materialId) || (parsedRow && (parsedRow.name !== name || parsedRow.identityName !== identityName || parsedRow.revisionHash !== revisionHash || parsedRow.contentContextHash !== contentContextHash))) throw new Error("Mismatched publication preview");
    return { materialId, name, identityName, row: parsedRow, revisionHash, contentContextHash, snapshotHash: hash(item.snapshot_hash), errors: list(item.errors, 100, code), warnings };
  });
  unique(items.map((item) => item.materialId));
  const canPrepare = boolean(result.can_prepare);
  if (!expected.length || items.length !== expected.length || items.some((item) => !expected.includes(item.materialId)) || canPrepare !== items.every((item) => !item.errors.length) || (canPrepare && items.some((item) => !item.row))) throw new Error("Inconsistent publication preview");
  return { items, canPrepare, previewHash: hash(result.preview_hash) };
}
export type PublicationPreview = ReturnType<typeof publicationPreviewFromDto>;
function summary(value: unknown) {
  const item = record(value), createdAt = text(item.created_at, 64);
  if (item.status !== "PREPARED" || !Number.isFinite(Date.parse(createdAt))) throw new Error("Invalid publication batch");
  return { id: uuid(item.id), actorId: uuid(item.actor_id), status: "PREPARED" as const, rowCount: integer(item.row_count, 1, 100), snapshotHash: hash(item.snapshot_hash), csvSha256: hash(item.csv_sha256), createdAt };
}
export function publicationBatchFromDto(value: unknown) {
  const item = record(value), batch = summary(value), warnings = list(item.warnings, 300, warning);
  const items = list(item.items, 100, (value) => {
    const item = record(value), materialId = uuid(item.material_id);
    return { materialId, ordinal: integer(item.ordinal, 1, 100), snapshotHash: hash(item.snapshot_hash), row: row(item.row, materialId),
      technicalApprovalId: uuid(item.technical_approval_id), publicationApprovalId: uuid(item.publication_approval_id), contentApprovalId: uuid(item.content_approval_id), metadataSnapshotId: uuid(item.metadata_snapshot_id) };
  });
  unique(items.map((item) => item.materialId));
  const warningsAcknowledged = boolean(item.warnings_acknowledged);
  if (items.length !== batch.rowCount || items.some((item, index) => item.ordinal !== index + 1) || warnings.some((entry) => !items.some((item) => item.materialId === entry.materialId)) || (warnings.length && !warningsAcknowledged)) throw new Error("Inconsistent publication batch");
  return { ...batch, reason: text(item.reason, 2000), warningsAcknowledged, warnings, items };
}
export type PublicationBatch = ReturnType<typeof publicationBatchFromDto>;
export interface PublicationCreate { material_ids: string[]; idempotency_key: string; expected_preview_hash: string; reason: string; warnings_acknowledged: boolean; }
export const publicationClient = {
  async preview(materialIds: string[]) { return publicationPreviewFromDto(await request("/publication-batches/preview", "POST", { material_ids: materialIds.map(uuid) }), materialIds); },
  async create(payload: PublicationCreate) {
    const batch = publicationBatchFromDto(await request("/publication-batches", "POST", payload));
    if (batch.snapshotHash !== payload.expected_preview_hash || batch.reason !== payload.reason || batch.warningsAcknowledged !== payload.warnings_acknowledged || batch.items.length !== payload.material_ids.length || batch.items.some((item) => !payload.material_ids.includes(item.materialId))) throw new Error("Mismatched saved publication batch");
    return batch;
  },
  async detail(id: string) { const batch = publicationBatchFromDto(await request(`/publication-batches/${uuid(id)}`)); if (batch.id !== id) throw new Error("Wrong publication batch"); return batch; },
  async history(after: string | null = null) {
    const page = record(await request(`/publication-batches${after ? `?after=${uuid(after)}` : ""}`));
    const items = list(page.items, 20, summary); unique(items.map((item) => item.id));
    const nextCursor = page.next_cursor === null ? null : uuid(page.next_cursor);
    if (nextCursor && (!items.length || items[items.length - 1].id !== nextCursor || nextCursor === after)) throw new Error("Invalid publication cursor");
    return { items, nextCursor };
  },
  async csv(batch: Pick<PublicationBatch, "id" | "csvSha256">, signal: AbortSignal) {
    const expectedHash = hash(batch.csvSha256), sentGeneration = sessionGeneration();
    const response = await fetch(apiUrl(`/publication-batches/${uuid(batch.id)}/csv`), { credentials: "same-origin", cache: "no-store", headers: { Accept: "text/csv" }, signal });
    if (!response.ok) {
      const body: unknown = await response.json().catch(() => null);
      notifySessionInvalidation(response.status, body, sentGeneration); throw responseError(response.status, body);
    }
    const declared = response.headers.get("Content-Length"), max = 32 * 1024 ** 2;
    if (response.headers.get("Content-Type")?.split(";")[0] !== "text/csv" || response.headers.get("X-Content-SHA256") !== expectedHash || !response.body || (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > max))) {
      await response.body?.cancel(); throw new Error("Invalid publication download");
    }
    const reader = response.body.getReader(), chunks: Uint8Array[] = []; let length = 0, finished = false;
    try {
      while (true) { const { done, value } = await reader.read(); if (done) { finished = true; break; } length += value.length; if (length > max) throw new Error("Publication download too large"); chunks.push(value); }
    } finally { try { if (!finished) await reader.cancel(); } finally { reader.releaseLock(); } }
    const bytes = new Uint8Array(length); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (byte) => byte.toString(16).padStart(2, "0")).join("");
    if (signal.aborted || digest !== expectedHash || (declared !== null && Number(declared) !== length) || bytes[0] !== 239 || bytes[1] !== 187 || bytes[2] !== 191 || !new TextDecoder("utf-8", { fatal: true }).decode(bytes).startsWith(columns.join(";") + "\r\n")) throw new Error("Publication download integrity check failed");
    return new Blob([bytes], { type: "text/csv;charset=utf-8" });
  },
};
