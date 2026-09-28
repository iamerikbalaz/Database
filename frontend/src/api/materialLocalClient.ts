import { request } from "./client";
import { boolean, nullable, record, string, uuid } from "./dto";
import type { Material } from "./materialDto";

function checkResult(input: unknown) {
  const value = record(input);
  if (!Array.isArray(value.issues) || value.issues.length > 4096) throw new Error("Invalid check issues");
  const status = value.status;
  if (status !== "NOT_CHECKED" && status !== "OK" && status !== "ISSUES") throw new Error("Invalid check status");
  return { materialId: uuid(value.material_id), status, checkedAt: string(value.checked_at),
    updatedAt: string(value.updated_at), profile: string(value.profile), complete: boolean(value.complete),
    report: string(value.report), issues: value.issues.map(string) };
}

export type AutomaticFileCheckResult = ReturnType<typeof checkResult>;

export const materialLocalClient = {
  async info(id: string) {
    const value = record(await request(`/materials/${uuid(id)}/local-files`));
    return { absolutePath: nullable(value.absolute_path), canOpen: boolean(value.can_open), canMove: boolean(value.can_move) };
  },
  async openFolder(id: string) { await request(`/materials/${uuid(id)}/local-files/open-folder`, "POST"); },
  async openMetadata(id: string) { await request(`/materials/${uuid(id)}/local-files/open-metadata`, "POST"); },
  async destination(id: string) {
    const value = record(await request(`/materials/${uuid(id)}/local-files/select-destination`, "POST"));
    return { absolutePath: nullable(value.destination_path), parent: nullable(value.target_parent) };
  },
  async check(id: string) {
    return checkResult(await request(`/materials/${uuid(id)}/check-data`, "POST"));
  },
  async checkMany(materials: Pick<Material, "id" | "updatedAt">[], openReport = true) {
    if (materials.length < 1 || materials.length > 100 || new Set(materials.map(item => item.id)).size !== materials.length) throw new Error("Select between 1 and 100 distinct materials");
    const value = record(await request("/materials/check-data", "POST", {
      materials: materials.map(item => ({ id: uuid(item.id), expected_updated_at: item.updatedAt })), open_report: openReport,
    }));
    if (!Array.isArray(value.items) || value.items.length !== materials.length) throw new Error("Invalid bulk check result");
    const items = value.items.map(checkResult);
    if (new Set(items.map(item => item.materialId)).size !== materials.length || items.some(item => !materials.some(material => material.id === item.materialId))) throw new Error("Invalid bulk check selection");
    return { items, report: string(value.report), reportPath: nullable(value.report_path), reportOpened: boolean(value.report_opened) };
  },
};
