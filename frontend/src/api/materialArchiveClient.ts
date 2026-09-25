import { request } from "./client";
import { boolean, nullable, record, string, uuid } from "./dto";
import { resourceHistorySchema } from "./resourceHistoryClient";

export type LifecycleAction = "ARCHIVE" | "RESTORE";
function integer(input: unknown, min = 0, max = 2147483647) {
  if (typeof input !== "number" || !Number.isSafeInteger(input) || input < min || input > max) throw new Error("Invalid lifecycle version");
  return input;
}
function digest(input: unknown) {
  const value = string(input);
  if (!/^[a-f0-9]{64}$/.test(value)) throw new Error("Invalid lifecycle digest");
  return value;
}
function timestamp(input: unknown) {
  const value = string(input);
  if (!/T.*(?:Z|[+-]\d\d:\d\d)$/.test(value) || !Number.isFinite(Date.parse(value))) throw new Error("Invalid lifecycle date");
  return value;
}
function key(input: unknown) {
  const value = uuid(input);
  if (value.replaceAll("-", "") === "0".repeat(32)) throw new Error("A nonzero request key is required");
  return value;
}
function action(input: unknown): LifecycleAction {
  if (input !== "ARCHIVE" && input !== "RESTORE") throw new Error("Invalid lifecycle action");
  return input;
}
function reason(input: unknown) {
  const value = string(input).trim();
  if (!value || [...value].length > 2000 || [...value].some((char) => char.charCodeAt(0) < 32 && !"\n\r\t".includes(char) || char.charCodeAt(0) === 127)) throw new Error("Enter a readable reason of at most 2000 characters");
  return value;
}
export function archiveDetail(input: unknown, materialId?: string) {
  const value = record(input), raw = record(value.material), id = uuid(raw.id);
  if (materialId && id !== uuid(materialId) || Object.keys(raw).sort().join() !== ["id", ...Object.keys(resourceHistorySchema.MATERIAL)].sort().join()) throw new Error("Invalid archived material");
  for (const [field, [, parse]] of Object.entries(resourceHistorySchema.MATERIAL)) parse(raw[field]);
  const version = integer(value.version), isArchived = boolean(value.is_archived), changedAt = value.changed_at === null ? null : timestamp(value.changed_at);
  if (isArchived !== (version % 2 === 1) || (version === 0) !== (changedAt === null)
      || isArchived && (raw.is_published || raw.publication_status !== "NOT_PUBLISHED" || raw.workflow_status !== "IN_PROGRESS" || raw.validation_status !== "NOT_CHECKED")) throw new Error("Invalid archive state");
  return { id, name: string(raw.material_name), technicalIdentity: string(raw.technical_identity), folderPath: nullable(raw.folder_path),
    assignedProcessorId: uuid(raw.assigned_processor_id), projectId: raw.project_id === null ? null : uuid(raw.project_id), brandId: uuid(raw.published_brand_id),
    sequenceNumber: integer(raw.sequence_number, 1, 9999), isArchived, version, changedAt };
}
export type ArchiveDetail = ReturnType<typeof archiveDetail>;
export function archivePreview(input: unknown, materialId: string, expectedAction: LifecycleAction) {
  const value = record(input), detail = archiveDetail(value, materialId), operation = action(value.action);
  const canApply = boolean(value.can_apply), blockedCode = nullable(value.blocked_code);
  if (operation !== expectedAction || canApply !== (blockedCode === null)
      || blockedCode !== null && !/^[A-Z_]{1,100}$/.test(blockedCode)
      || canApply && (detail.isArchived !== (operation === "RESTORE") || detail.version >= 2147483647)) throw new Error("Invalid lifecycle preview");
  return { ...detail, action: operation, inputHash: digest(value.input_sha256), canApply, blockedCode };
}
export type ArchivePreview = ReturnType<typeof archivePreview>;
export interface LifecycleRequest {
  action: LifecycleAction;
  request_key: string;
  expected_version: number;
  expected_input_sha256: string;
  reason: string;
  acknowledge: true;
}
export function lifecycleRequest(preview: ArchivePreview, explanation: string, requestKey: string = crypto.randomUUID()): LifecycleRequest {
  if (!preview.canApply || preview.isArchived !== (preview.action === "RESTORE")) throw new Error("Review current eligibility first");
  return { action: action(preview.action), request_key: key(requestKey), expected_version: integer(preview.version, 0, 2147483646),
    expected_input_sha256: digest(preview.inputHash), reason: reason(explanation), acknowledge: true };
}
export function lifecycleEvent(input: unknown, materialId: string) {
  const value = record(input), version = integer(value.version, 1), operation = action(value.action), explanation = reason(value.reason);
  if (uuid(value.material_id) !== uuid(materialId) || (operation === "ARCHIVE") !== (version % 2 === 1) || explanation !== value.reason) throw new Error("Invalid lifecycle event");
  return { id: uuid(value.id), materialId, actorId: uuid(value.actor_id), action: operation, version,
    requestKey: key(value.request_key), requestHash: digest(value.request_sha256), inputHash: digest(value.input_sha256),
    reason: explanation, reviewGeneration: integer(value.review_generation, 1, Number.MAX_SAFE_INTEGER), createdAt: timestamp(value.created_at) };
}
export type LifecycleEvent = ReturnType<typeof lifecycleEvent>;
function result(input: unknown, preview: ArchivePreview, actorId: string, body: LifecycleRequest) {
  const event = lifecycleEvent(record(input).event, preview.id);
  if (event.actorId !== uuid(actorId) || event.requestKey !== body.request_key || event.action !== body.action
      || event.version !== body.expected_version + 1 || event.inputHash !== body.expected_input_sha256 || event.reason !== body.reason) throw new Error("Unrelated lifecycle result");
  return event;
}
export function archivePage(input: unknown, after: string | null = null) {
  const value = record(input);
  if (!Array.isArray(value.items) || value.items.length > 20) throw new Error("Invalid archive page");
  const items = value.items.map((item) => archiveDetail(item)), nextCursor = value.next_cursor === null ? null : uuid(value.next_cursor);
  if (items.some((item, index) => !item.isArchived || after && item.id <= after || index > 0 && items[index - 1].id >= item.id)
      || nextCursor && (items.length !== 20 || nextCursor !== items.at(-1)?.id)) throw new Error("Invalid archive ordering");
  return { items, nextCursor };
}
export function lifecyclePage(input: unknown, materialId: string, after: string | null = null) {
  const value = record(input);
  if (uuid(value.material_id) !== uuid(materialId) || !Array.isArray(value.items) || value.items.length > 20) throw new Error("Invalid lifecycle page");
  const items = value.items.map((item) => lifecycleEvent(item, materialId)), nextCursor = value.next_cursor === null ? null : uuid(value.next_cursor);
  if (new Set(items.map((item) => item.id)).size !== items.length
      || items.some((item, index) => item.id === after || index > 0 && items[index - 1].version !== item.version + 1)
      || nextCursor && (items.length !== 20 || nextCursor !== items.at(-1)?.id || nextCursor === after)) throw new Error("Invalid lifecycle ordering");
  return { items, nextCursor };
}
function validate(preview: ArchivePreview, body: LifecycleRequest) {
  if (JSON.stringify(lifecycleRequest(preview, body.reason, body.request_key)) !== JSON.stringify(body)) throw new Error("Changed lifecycle request");
}
export const materialArchiveClient = {
  async detail(id: string) { return archiveDetail(await request(`/material-archives/${uuid(id)}`), id); },
  async preview(id: string, operation: LifecycleAction) {
    return archivePreview(await request(`/material-archives/${uuid(id)}/preview?action=${action(operation)}`), id, operation);
  },
  async list(after: string | null = null) {
    return archivePage(await request(`/material-archives${after ? `?after=${uuid(after)}` : ""}`), after);
  },
  async history(id: string, after: string | null = null) {
    return lifecyclePage(await request(`/material-archives/${uuid(id)}/history${after ? `?after=${uuid(after)}` : ""}`), id, after);
  },
  async command(preview: ArchivePreview, actorId: string, body: LifecycleRequest) {
    validate(preview, body);
    return result(await request(`/material-archives/${uuid(preview.id)}/commands`, "POST", body), preview, actorId, body);
  },
  async recover(preview: ArchivePreview, actorId: string, body: LifecycleRequest) {
    validate(preview, body);
    return result(await request(`/material-archives/${uuid(preview.id)}/commands/${key(body.request_key)}`), preview, actorId, body);
  },
};
