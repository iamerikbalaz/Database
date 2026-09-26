import { request } from "./client";
import { historyPage, historyQuery } from "./historyPage";
import { boolean, nullable, record, string, uuid } from "./dto";

function integer(value: unknown, minimum = 0) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > 2147483647) throw new Error("Invalid catalog number");
  return value;
}
function list<T>(value: unknown, parse: (item: unknown) => T, maximum = 100): T[] {
  if (!Array.isArray(value) || value.length > maximum) throw new Error("Invalid catalog list");
  return value.map(parse);
}
export function catalogValue(input: unknown) {
  const item = record(input);
  return { id: uuid(item.id), value: string(item.value), version: integer(item.version, 1), active: boolean(item.is_active),
    brandId: item.brand_id === undefined ? null : uuid(item.brand_id),
    abbreviation: item.abbreviation === undefined ? null : nullable(item.abbreviation),
    createdAt: item.created_at === undefined ? null : nullable(item.created_at) };
}
export type CatalogValue = ReturnType<typeof catalogValue>;
export function contentFromDto(input: unknown) {
  const item = record(input);
  if (item.content_status !== "EMPTY" && item.content_status !== "MANUAL_DRAFT" && item.content_status !== "AI_DRAFT" && item.content_status !== "APPROVED") throw new Error("Unknown content status");
  const source = item.ai_provenance === undefined ? null : record(item.ai_provenance);
  const aiProvenance = source === null ? null : { draftId: uuid(source.draft_id), provider: string(source.provider), model: string(source.model),
    promptVersion: string(source.prompt_version), contextHash: string(source.context_hash), edited: boolean(source.edited) };
  if ((item.content_status === "AI_DRAFT" && !aiProvenance) || (aiProvenance && !/^[a-f0-9]{64}$/.test(aiProvenance.contextHash))) throw new Error("Invalid AI content provenance");
  return { materialId: uuid(item.material_id), revision: integer(item.revision), description: nullable(item.description),
    credits: item.credits === null ? null : integer(item.credits), tags: list(item.tags, string),
    categories: list(item.categories, catalogValue), collections: list(item.collections, catalogValue), status: item.content_status,
    ...(aiProvenance ? { aiProvenance } : {}) };
}
export type MaterialContent = ReturnType<typeof contentFromDto>;
export interface ContentPayload {
  idempotency_key: string; expected_revision: number; description: string | null; credits: number | null;
  tags: string[]; category_ids: string[]; collection_ids: string[]; reason: string;
}
export interface CatalogCreate { idempotency_key: string; value: string; brand_id?: string; abbreviation?: string | null; }
export interface CatalogActivity { idempotency_key: string; expected_version: number; is_active: boolean; reason: string; }
export type CatalogTableUpdate = { idempotency_key: string; expected_version: number; reason: string } &
  ({ is_active: boolean } | { abbreviation: string | null });
export type CatalogKind = "online-categories" | "collections";
export const catalogClient = {
  async categories() { return list(await request("/online-categories"), catalogValue, 10000); },
  async collections(brandId?: string) { return list(await request("/collections" + (brandId ? `?brand_id=${uuid(brandId)}` : "")), catalogValue, 10000); },
  async create(kind: CatalogKind, payload: CatalogCreate) { return catalogValue(await request(`/${kind}`, "POST", payload)); },
  async activity(kind: CatalogKind, id: string, payload: CatalogActivity) { return catalogValue(await request(`/${kind}/${uuid(id)}`, "PATCH", payload)); },
  async table(kind: CatalogKind, id: string, payload: CatalogTableUpdate) { return catalogValue(await request(`/${kind}/${uuid(id)}/table`, "PATCH", payload)); },
  async content(id: string) { return contentFromDto(await request(`/materials/${uuid(id)}/content`)); },
  async save(id: string, payload: ContentPayload) { return contentFromDto(await request(`/materials/${uuid(id)}/content`, "POST", payload)); },
  async history(id: string, after: string | null = null) { return historyPage(await request(`/materials/${uuid(id)}/content-history${historyQuery(after)}`), after, (input) => {
    const item = record(input);
    const revision = integer(item.revision, 1), snapshot = contentFromDto(item.snapshot);
    if (snapshot.materialId !== uuid(id) || snapshot.revision !== revision) throw new Error("Mismatched content history");
    return { id: uuid(item.id), revision, actorId: uuid(item.actor_id), reason: string(item.reason),
      createdAt: string(item.created_at), snapshot };
  }); },
};
