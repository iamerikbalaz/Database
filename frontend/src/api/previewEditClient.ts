import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";

export type PreviewEditRequest = {
  materials: { id: string; expected_updated_at: string }[];
  action: "RENAME" | "DELETE" | "BULK";
  filename?: string; new_name?: string; find?: string; replace?: string; delete_containing?: string;
  case_sensitive?: boolean;
};
export type PreviewEditPlan = {
  proposal_hash: string; can_apply: boolean; total_renames: number; total_deletes: number;
  items: { material_id: string; identity: string; rename_count: number; delete_count: number;
    changes: { from: string; to: string | null }[]; issues: { code: string; message: string }[] }[];
};
export type PreviewEditResult = {
  id: string; status: "RUNNING" | "COMPLETED" | "PARTIAL" | "REJECTED" | "RECOVERY_REQUIRED";
  items: { material_id: string; status: "RUNNING" | "COMPLETED" | "REJECTED" | "RECOVERY_REQUIRED";
    renamed: number; deleted: number; error_code: string | null }[];
};
export type PreviewEditConfirmation = PreviewEditRequest & { idempotency_key: string; expected_proposal_hash: string; confirmed: true };
function count(value: unknown) { if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error("Invalid preview count"); return value; }
function array(value: unknown): unknown[] { if (!Array.isArray(value) || value.length > 10000) throw new Error("Invalid preview list"); return value; }
function parsePlan(input: unknown): PreviewEditPlan {
  const value = record(input);
  return { proposal_hash: string(value.proposal_hash), can_apply: boolean(value.can_apply), total_renames: count(value.total_renames), total_deletes: count(value.total_deletes),
    items: array(value.items).map(raw => { const item = record(raw); return { material_id: uuid(item.material_id), identity: string(item.identity), rename_count: count(item.rename_count), delete_count: count(item.delete_count),
      changes: array(item.changes).map(raw => { const change = record(raw); return { from: string(change.from), to: change.to === null ? null : string(change.to) }; }),
      issues: array(item.issues).map(raw => { const issue = record(raw); return { code: string(issue.code), message: string(issue.message) }; }) }; }) };
}
function parseResult(input: unknown): PreviewEditResult {
  const value = record(input), status = string(value.status);
  if (!["RUNNING", "COMPLETED", "PARTIAL", "REJECTED", "RECOVERY_REQUIRED"].includes(status)) throw new Error("Invalid preview operation status");
  return { id: uuid(value.id), status: status as PreviewEditResult["status"], items: array(value.items).map(raw => {
    const item = record(raw), status = string(item.status);
    if (!["RUNNING", "COMPLETED", "REJECTED", "RECOVERY_REQUIRED"].includes(status)) throw new Error("Invalid preview material status");
    return { material_id: uuid(item.material_id), status: status as PreviewEditResult["items"][number]["status"], renamed: count(item.renamed), deleted: count(item.deleted), error_code: item.error_code == null ? null : string(item.error_code) };
  }) };
}
export const previewEditClient = {
  async plan(body: PreviewEditRequest) { return parsePlan(await request("/material-preview-edits/plan", "POST", body)); },
  async apply(body: PreviewEditConfirmation) { return parseResult(await request("/material-preview-edits", "POST", body)); },
  async pending(materialIds: string[]) {
    const query = new URLSearchParams(); materialIds.forEach(id => query.append("material_ids", uuid(id)));
    return array(record(await request(`/material-preview-edits?${query}`)).items).map(parseResult);
  },
  async resume(id: string) { return parseResult(await request(`/material-preview-edits/${uuid(id)}/resume`, "POST", { confirmed: true })); },
};
