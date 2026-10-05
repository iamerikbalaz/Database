import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";

function text(value: unknown, max = 10000) { const result = string(value); if (result.length > max) throw new Error("Invalid export text"); return result; }
function nullableText(value: unknown, max = 10000) { return value === null ? null : text(value, max); }
function digest(value: unknown) { const result = text(value, 64); if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid export hash"); return result; }
function code(value: unknown) { const result = text(value, 100); if (!/^[A-Z][A-Z0-9_]*$/.test(result)) throw new Error("Invalid export code"); return result; }
function list<T>(value: unknown, max: number, parse: (item: unknown) => T): T[] { if (!Array.isArray(value) || value.length > max) throw new Error("Invalid export list"); return value.map(parse); }
function ids(value: unknown) { const result = list(value, 100, uuid); if (!result.length || new Set(result).size !== result.length) throw new Error("Invalid export selection"); return result; }
function sameSelection(actual: string[], expected: string[]) { if (actual.length !== expected.length || actual.some(id => !expected.includes(id))) throw new Error("Export selection changed"); }
function filename(value: unknown) { const name = text(value, 255); if (!name || /[\\/:]/.test(name) || [...name].some(char => char.charCodeAt(0) < 32) || name === "." || name === "..") throw new Error("Invalid export filename"); return name; }
function integer(value: unknown, max = Number.MAX_SAFE_INTEGER) { if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > max) throw new Error("Invalid export number"); return value; }
function dimension(value: unknown) { const result = text(value, 32); if (!/^\d+(\.\d{1,4})?$/.test(result) || Number(result) <= 0 || Number(result) >= 1e8) throw new Error("Invalid export dimension"); return result; }
export function localPublicationPreview(value: unknown, selected: string[]) {
  const dto = record(value);
  const items = list(dto.items, 100, value => {
    const item = record(value), materialId = uuid(item.material_id), name = text(item.name, 255), identity = nullableText(item.identity_name, 512);
    let row = null;
    if (item.row !== null) {
      const source = record(item.row), color = text(source.color, 7);
      if (!identity || !/^#[A-F0-9]{6}$/.test(color) || uuid(source.material_id) !== materialId || source.name !== name || source.identity_name !== identity) throw new Error("Invalid export row");
      row = { description: nullableText(source.description), credits: integer(source.credits, 2147483647), widthCm: dimension(source.width_cm), heightCm: dimension(source.height_cm),
        brandIdentifier: text(source.brand_identifier, 255), categories: list(source.categories, 100, value => text(value, 255)), color, tags: list(source.tags, 100, value => text(value, 100)) };
      if (!row.categories.length) throw new Error("Missing export category");
    }
    return { materialId, name, identity, row, errors: list(item.errors, 100, code), warnings: list(item.warnings, 100, value => {
      const warning = record(value); return { code: code(warning.code), fields: list(warning.fields, 9, value => text(value, 100)) };
    }) };
  });
  sameSelection(ids(items.map(item => item.materialId)), ids(selected));
  const canPrepare = boolean(dto.can_prepare);
  if (items.some(item => item.identity === null && !item.errors.length) || canPrepare !== items.every(item => !item.errors.length) || canPrepare && items.some(item => !item.row)) throw new Error("Invalid export review");
  return { items, canPrepare, previewHash: digest(dto.preview_hash) };
}
export type LocalPublicationPreview = ReturnType<typeof localPublicationPreview>;
export function localPublicationJob(value: unknown, selected: string[]) {
  const item = record(value), status = text(item.status, 20);
  if (status !== "RUNNING" && status !== "COMPLETED" && status !== "FAILED") throw new Error("Invalid export status");
  const materialIds = ids(item.material_ids); sameSelection(materialIds, ids(selected));
  const archives = list(item.archives, 1000, value => { const archive = record(value); return { name: filename(archive.name), sha256: digest(archive.sha256), sizeBytes: integer(archive.size_bytes) }; });
  if (new Set(archives.map(item => item.name.toLowerCase())).size !== archives.length) throw new Error("Duplicate archive");
  const outputPath = nullableText(item.output_path, 4096), csvName = item.csv_name === null ? null : filename(item.csv_name);
  const errorCode = item.error_code === null ? null : code(item.error_code), published = boolean(item.published);
  const issues = list(item.issues ?? [], 100, value => {
    const group = record(value), materialId = uuid(group.material_id);
    if (!materialIds.includes(materialId)) throw new Error("Unexpected export issue");
    return { materialId, issues: list(group.issues, 4096, value => {
      const issue = record(value); return { code: code(issue.code), path: issue.path == null ? null : text(issue.path, 4096) };
    }) };
  });
  if (status === "COMPLETED" && (!outputPath || !csvName || !archives.length || errorCode) || status !== "COMPLETED" && published) throw new Error("Incomplete export receipt");
  return { id: uuid(item.id), status, materialIds, outputPath, csvName, archives, errorCode, published, issues };
}
export type LocalPublicationJob = ReturnType<typeof localPublicationJob>;
export type LocalExportRequest = { material_ids: string[]; expected_preview_hash: string; destination_token: string; idempotency_key: string };
export const localPublicationClient = {
  async preview(materialIds: string[]) { return localPublicationPreview(await request("/local-publication/preview", "POST", { material_ids: ids(materialIds) }), materialIds); },
  async destination() {
    const item = record(await request("/local-publication/destination", "POST"));
    const token = item.destination_token === null ? null : text(item.destination_token, 256), path = nullableText(item.destination_path, 4096);
    if ((token === null) !== (path === null) || token !== null && !token) throw new Error("Invalid destination");
    return token && path ? { token, path } : null;
  },
  async create(body: LocalExportRequest) {
    const result = localPublicationJob(await request("/local-publication/exports", "POST", body), body.material_ids);
    if (result.id !== body.idempotency_key) throw new Error("Wrong export receipt"); return result;
  },
  async detail(id: string, selected: string[], signal?: AbortSignal) {
    const result = localPublicationJob(await request(`/local-publication/exports/${uuid(id)}`, "GET", undefined, undefined, signal), selected);
    if (result.id !== id) throw new Error("Wrong export receipt"); return result;
  },
  async markPublished(id: string, selected: string[], key: string) {
    const item = record(await request(`/local-publication/exports/${uuid(id)}/published`, "POST", { idempotency_key: uuid(key) }));
    sameSelection(ids(item.material_ids), selected); if (item.published !== true) throw new Error("Published was not confirmed");
  },
};
