import { request } from "./client";
import { boolean, nullable, record, string, uuid } from "./dto";
import { contentFromDto } from "./catalogClient";

function text(value: unknown, max: number) {
  const result = string(value); if (result.length > max) throw new Error("Invalid AI context text"); return result;
}
function integer(value: unknown, max = 2147483647, min = 0) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) throw new Error("Invalid AI context number"); return value;
}
function list<T>(value: unknown, max: number, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length > max) throw new Error("Invalid AI context list"); return value.map(parse);
}
function hash(value: unknown) { const result = string(value); if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid AI context digest"); return result; }
function unique(values: string[]) { if (new Set(values).size !== values.length) throw new Error("Duplicate AI references"); }
function sourceUrl(value: unknown) {
  const raw = text(value, 2048), url = new URL(raw);
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || /[\p{C}\s\\]/u.test(raw)) throw new Error("Invalid AI source URL"); return raw;
}
function source(value: unknown) { const item = record(value); return { id: uuid(item.id), url: sourceUrl(item.url) }; }
function named(value: unknown) { const item = record(value); return { id: uuid(item.id), value: text(item.value, 255) }; }
function context(value: unknown, materialId: string) {
  const item = record(value), brand = record(item.brand);
  if (uuid(item.material_id) !== uuid(materialId)) throw new Error("Wrong AI context material");
  const sourceUrls = list(item.source_urls, 20, source), categories = list(item.categories, 100, named), collections = list(item.collections, 100, named);
  for (const items of [sourceUrls, categories, collections]) unique(items.map((entry) => entry.id));
  return { materialId, name: text(item.name, 255), brand: { id: uuid(brand.id), name: text(brand.name, 255) }, categories, collections, sourceUrls };
}
export function publishingContextFromDto(value: unknown, materialId: string) {
  const item = record(value); return { context: context(item.context, materialId), contextHash: hash(item.context_hash), contentRevision: integer(item.content_revision) };
}
export type PublishingContext = ReturnType<typeof publishingContextFromDto>;
export function sourceFromDto(value: unknown) { const item = record(value); return { ...source(item), version: integer(item.version, 2147483647, 1), active: boolean(item.is_active) }; }
export type ContentSource = ReturnType<typeof sourceFromDto>;
export function aiDraftFromDto(value: unknown, materialId: string) {
  const item = record(value);
  if (uuid(item.material_id) !== uuid(materialId) || item.status !== "AI_DRAFT") throw new Error("Wrong AI proposal");
  const ctx = context(item.context, materialId), sources = list(item.source_link_ids, 20, uuid), tags = list(item.tags, 100, (value) => text(value, 100));
  unique(sources);
  if (sources.some((id) => !ctx.sourceUrls.some((entry) => entry.id === id)) || tags.some((tag) => !tag || tag.includes(":"))) throw new Error("Invalid AI proposal references");
  const description = nullable(item.description);
  if (description !== null && description.length > 10000) throw new Error("Oversize AI proposal");
  const createdAt = string(item.created_at); if (!Number.isFinite(Date.parse(createdAt))) throw new Error("Invalid AI proposal timestamp");
  return { id: uuid(item.id), materialId, actorId: uuid(item.actor_id), context: ctx, contextHash: hash(item.context_hash),
    contentRevision: integer(item.content_revision), provider: text(item.provider, 100), model: text(item.model, 100), promptVersion: text(item.prompt_version, 100),
    description, tags, sourceIds: sources, reason: text(item.reason, 2000), createdAt,
    ...(item.context_is_current === undefined ? {} : { contextIsCurrent: boolean(item.context_is_current) }) };
}
export type AiDraft = ReturnType<typeof aiDraftFromDto>;
export interface AiProposalPayload {
  idempotency_key: string; expected_context_hash: string; provider: string; model: string; prompt_version: string;
  description: string | null; tags: string[]; source_link_ids: string[]; reason: string;
}
export interface AiAdoptionPayload { idempotency_key: string; expected_revision: number; expected_context_hash: string; description: string | null; tags: string[]; reason: string; }
export const aiContentClient = {
  async context(materialId: string) { return publishingContextFromDto(await request(`/materials/${uuid(materialId)}/publishing-context`), materialId); },
  async sources(materialId: string) { return list(await request(`/materials/${uuid(materialId)}/content-sources`), 100, sourceFromDto); },
  async approveSource(materialId: string, payload: { idempotency_key: string; url: string; reason: string }) {
    return sourceFromDto(await request(`/materials/${uuid(materialId)}/content-sources`, "POST", payload));
  },
  async sourceActivity(materialId: string, sourceId: string, payload: { idempotency_key: string; expected_version: number; is_active: boolean; reason: string }) {
    return sourceFromDto(await request(`/materials/${uuid(materialId)}/content-sources/${uuid(sourceId)}`, "PATCH", payload));
  },
  async submit(materialId: string, payload: AiProposalPayload) {
    return aiDraftFromDto(await request(`/materials/${uuid(materialId)}/content-drafts`, "POST", payload), materialId);
  },
  async drafts(materialId: string, after: string | null = null) {
    const query = after ? `?after=${uuid(after)}` : "";
    const item = record(await request(`/materials/${uuid(materialId)}/content-drafts${query}`));
    const items = list(item.items, 20, (value) => aiDraftFromDto(value, materialId)); unique(items.map((entry) => entry.id));
    if (items.some((entry) => entry.contextIsCurrent === undefined)) throw new Error("Missing AI context status");
    return { items, nextCursor: item.next_cursor === null ? null : uuid(item.next_cursor) };
  },
  async adopt(materialId: string, draftId: string, payload: AiAdoptionPayload) {
    const result = contentFromDto(await request(`/materials/${uuid(materialId)}/content-drafts/${uuid(draftId)}/adopt`, "POST", payload));
    if (result.materialId !== uuid(materialId) || result.aiProvenance?.draftId !== uuid(draftId) || result.revision !== payload.expected_revision + 1 || result.status !== "AI_DRAFT") throw new Error("Mismatched AI adoption result"); return result;
  },
};
