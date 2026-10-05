import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";

export type MaterialDeletionMode = "RECORD_ONLY" | "RECORD_AND_FILES";
export type MaterialDeletionRequest = { materials: { id: string; expected_updated_at: string }[]; mode: MaterialDeletionMode };
export type MaterialDeletionConfirmation = MaterialDeletionRequest & { idempotency_key: string; expected_proposal_hash: string; confirmed: true };
export type MaterialDeletionPlan = { proposal_hash: string; can_apply: boolean; mode: MaterialDeletionMode; total: number; warnings: string[];
  items: { material_id: string; material_name: string; identity: string | null; folder_path: string | null; file_count: number | null; issues: { code: string; message: string }[] }[] };
export type MaterialDeletionResult = { id: string; status: "RUNNING" | "RECOVERY_REQUIRED" | "COMPLETED" | "PARTIAL" | "REJECTED";
  mode: MaterialDeletionMode; deleted_count: number; quarantine_retained: boolean;
  items: { material_id: string; material_name: string; identity: string | null; status: "RUNNING" | "RECOVERY_REQUIRED" | "COMPLETED" | "REJECTED"; error_code: string | null }[] };
function array(input: unknown): unknown[] { if (!Array.isArray(input) || input.length > 100) throw new Error("Invalid deletion list"); return input; }
function count(input: unknown) { if (typeof input !== "number" || !Number.isSafeInteger(input) || input < 0) throw new Error("Invalid deletion count"); return input; }
function mode(input: unknown): MaterialDeletionMode { if (input !== "RECORD_ONLY" && input !== "RECORD_AND_FILES") throw new Error("Invalid deletion mode"); return input; }
function nullable(input: unknown) { return input === null ? null : string(input); }
function parsePlan(input: unknown): MaterialDeletionPlan {
  const value = record(input), hash = string(value.proposal_hash);
  if (!/^[a-f0-9]{64}$/.test(hash)) throw new Error("Invalid deletion proposal");
  const items = array(value.items).map(raw => { const item = record(raw); return { material_id: uuid(item.material_id), material_name: string(item.material_name), identity: nullable(item.identity), folder_path: nullable(item.folder_path),
    file_count: item.file_count === null ? null : count(item.file_count), issues: array(item.issues).map(raw => { const issue = record(raw); return { code: string(issue.code), message: string(issue.message) }; }) }; });
  if (!items.length || count(value.total) !== items.length || new Set(items.map(item => item.material_id)).size !== items.length) throw new Error("Invalid deletion selection");
  return { proposal_hash: hash, can_apply: boolean(value.can_apply), total: items.length, mode: mode(value.mode), warnings: array(value.warnings).map(string), items };
}
function parseResult(input: unknown): MaterialDeletionResult {
  const value = record(input), status = string(value.status);
  if (!["RUNNING", "RECOVERY_REQUIRED", "COMPLETED", "PARTIAL", "REJECTED"].includes(status)) throw new Error("Invalid deletion result");
  const items = array(value.items).map(raw => { const item = record(raw), status = string(item.status);
    if (!["RUNNING", "RECOVERY_REQUIRED", "COMPLETED", "REJECTED"].includes(status)) throw new Error("Invalid deletion item");
    return { material_id: uuid(item.material_id), material_name: string(item.material_name), identity: nullable(item.identity), status: status as MaterialDeletionResult["items"][number]["status"], error_code: nullable(item.error_code) }; });
  const deleted = count(value.deleted_count);
  if (!items.length || new Set(items.map(item => item.material_id)).size !== items.length || deleted !== items.filter(item => item.status === "COMPLETED").length) throw new Error("Invalid deletion count");
  const pending = items.some(item => item.status === "RUNNING" || item.status === "RECOVERY_REQUIRED");
  if ((status === "COMPLETED" && deleted !== items.length) || (status === "REJECTED" && (deleted !== 0 || pending)) ||
      (status === "PARTIAL" && (deleted === 0 || deleted === items.length || pending)) ||
      (["RUNNING", "RECOVERY_REQUIRED"].includes(status) && !pending)) throw new Error("Invalid deletion outcome");
  return { id: uuid(value.id), status: status as MaterialDeletionResult["status"], mode: mode(value.mode), deleted_count: deleted, quarantine_retained: boolean(value.quarantine_retained), items };
}
export const materialDeletionClient = {
  async plan(body: MaterialDeletionRequest) { return parsePlan(await request("/material-deletions/plan", "POST", body)); },
  async apply(body: MaterialDeletionConfirmation) { return parseResult(await request("/material-deletions", "POST", body)); },
  async pending(ids: string[]) { const query = new URLSearchParams(); array(ids).forEach(id => query.append("material_ids", uuid(id))); return array(record(await request(`/material-deletions?${query}`)).items).map(parseResult); },
  async get(id: string) { return parseResult(await request(`/material-deletions/${uuid(id)}`)); },
  async resume(id: string) { return parseResult(await request(`/material-deletions/${uuid(id)}/resume`, "POST", { confirmed: true })); },
};
