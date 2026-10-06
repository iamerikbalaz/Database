import { request } from "./client";
import { boolean, nullable, record, string, uuid } from "./dto";
import { automaticFileCheckStatuses } from "./materialDto";

function status(value: unknown) {
  if (!automaticFileCheckStatuses.some(item => item === value)) throw new Error("Invalid stored check status");
  return value as typeof automaticFileCheckStatuses[number];
}
function text(value: unknown, limit: number) {
  const result = string(value); if (result.length > limit) throw new Error("Stored check report is too large"); return result;
}
function findings(value: unknown) {
  if (!Array.isArray(value) || value.length > 4096) throw new Error("Invalid stored check findings");
  return value.map(item => text(item, 4096));
}
export function storedFileCheckFromDto(value: unknown, materialId: string) {
  const body = record(value);
  if (uuid(body.material_id) !== uuid(materialId)) throw new Error("Wrong material in stored report");
  const currentStatus = status(body.current_status);
  if (body.report === null) return { materialId, currentStatus, report: null };
  const data = record(body.report), checkedAt = nullable(data.checked_at), profile = nullable(data.profile);
  if (checkedAt !== null && !Number.isFinite(Date.parse(checkedAt))) throw new Error("Invalid check timestamp");
  if (profile !== null && profile.length > 32) throw new Error("Invalid check profile");
  return { materialId, currentStatus, report: { status: status(data.status), checkedAt, profile,
    complete: boolean(data.complete), isCurrent: boolean(data.is_current), issues: findings(data.issues), warnings: findings(data.warnings), text: text(data.text, 1024 * 1024) } };
}
export type StoredFileCheck = ReturnType<typeof storedFileCheckFromDto>;
export const storedFileCheckClient = {
  async get(materialId: string) { return storedFileCheckFromDto(await request(`/materials/${uuid(materialId)}/automatic-file-check-report`), materialId); },
};
