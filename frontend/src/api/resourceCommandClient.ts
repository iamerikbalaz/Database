import { request } from "./client";
import { parseCompany, parseProject, parsePublishedBrand, record, string, uuid } from "./dto";
import { parseInternalUser, parseMaterial } from "./materialDto";

export type CommandKind = "COMPANY" | "BRAND" | "PROJECT" | "USER" | "MATERIAL";
export interface CommandScope { kind: CommandKind; action: "CREATED" | "UPDATED"; targetId: string | null; editorPath: string; }
const schemas = { COMPANY: parseCompany, BRAND: parsePublishedBrand, PROJECT: parseProject, USER: parseInternalUser, MATERIAL: parseMaterial };
const segments = { COMPANY: "companies", BRAND: "brands", PROJECT: "projects", USER: "accounts", MATERIAL: "materials" };
function digest(input: unknown) { const value = string(input); if (!/^[a-f0-9]{64}$/.test(value)) throw new Error("Invalid receipt digest"); return value; }

function canonical(value: unknown): string {
  if (value === null || typeof value === "string" || typeof value === "boolean" || typeof value === "number" && Number.isSafeInteger(value)) return JSON.stringify(value);
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const data = value as Record<string, unknown>;
    return "{" + Object.keys(data).sort().map((key) => JSON.stringify(key) + ":" + canonical(data[key])).join(",") + "}";
  }
  throw new Error("Invalid ordinary request value");
}
export async function resourceRequestHash(scope: CommandScope, payload: object) {
  const text = canonical({ schema_version: 1, kind: scope.kind, action: scope.action, target_id: scope.targetId === null ? null : uuid(scope.targetId), payload })
    .replace(/[\u007f-\uffff]/g, (character) => "\\u" + character.charCodeAt(0).toString(16).padStart(4, "0"));
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function resourceReceipt(input: unknown, scope: CommandScope, actorId: string, key: string, expectedRequestHash: string) {
  const value = record(input), resourceId = uuid(value.resource_id);
  if (uuid(value.actor_id) !== uuid(actorId) || uuid(value.request_key) !== uuid(key) || digest(value.request_hash) !== digest(expectedRequestHash) || value.kind !== scope.kind || value.action !== scope.action ||
      (scope.action === "UPDATED" && resourceId !== uuid(scope.targetId)) || (scope.action === "CREATED" && scope.targetId !== null)) throw new Error("Mismatched saved request");
  const raw = record(value.response), response = schemas[scope.kind](raw);
  if (response.id !== resourceId || Object.keys(raw).sort().join() !== Object.keys(response).sort().join()) throw new Error("Invalid saved response");
  const createdAt = string(value.created_at);
  if (!/T.*(?:Z|[+-]\d\d:\d\d)$/.test(createdAt) || !Number.isFinite(Date.parse(createdAt))) throw new Error("Invalid receipt time");
  return { id: uuid(value.id), resourceId, requestHash: digest(value.request_hash), responseHash: digest(value.response_hash), createdAt,
    path: scope.kind === "USER" ? "/accounts" : `/${segments[scope.kind]}/${resourceId}`,
    message: "Saved request recovered. Reload the record to see its current values." };
}
export const resourceCommandClient = {
  async recover(scope: CommandScope, actorId: string, key: string, requestHash: string) {
    return resourceReceipt(await request(`/resource-commands/${uuid(key)}`), scope, actorId, key, requestHash);
  },
};
