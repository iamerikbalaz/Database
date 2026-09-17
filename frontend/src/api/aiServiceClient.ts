import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";

function timestamp(value: unknown) { const result = string(value); if (!Number.isFinite(Date.parse(result))) throw new Error("Invalid credential time"); return result; }
function credential(value: unknown, materialId: string) {
  const item = record(value);
  if (uuid(item.material_id) !== uuid(materialId)) throw new Error("Wrong credential material");
  if (!Array.isArray(item.scopes) || item.scopes.length !== 2 || !item.scopes.includes("publishing-context:read") || !item.scopes.includes("content-drafts:write")) throw new Error("Unexpected service scope");
  const createdAt = timestamp(item.created_at), expiresAt = timestamp(item.expires_at), revokedAt = item.revoked_at === null ? null : timestamp(item.revoked_at);
  if (Date.parse(expiresAt) <= Date.parse(createdAt) || Date.parse(expiresAt) - Date.parse(createdAt) > 3600000 || (revokedAt && Date.parse(revokedAt) < Date.parse(createdAt))) throw new Error("Invalid credential lifetime");
  return { id: uuid(item.id), materialId, actorId: uuid(item.actor_id), createdAt, expiresAt, revokedAt };
}
export type AiCredential = ReturnType<typeof credential>;
export interface AiIssuePayload { idempotency_key: string; lifetime_seconds: number; reason: string; }
export interface AiRevokePayload { idempotency_key: string; reason: string; }
export const aiServiceClient = {
  async history(materialId: string, after: string | null = null) {
    const data = record(await request(`/materials/${uuid(materialId)}/ai-service-credentials` + (after ? `?after=${uuid(after)}` : "")));
    if (!Array.isArray(data.items) || data.items.length > 20) throw new Error("Invalid credential list");
    const items = data.items.map((item) => credential(item, materialId));
    if (new Set(items.map((item) => item.id)).size !== items.length) throw new Error("Duplicate credentials");
    return { items, nextCursor: data.next_cursor === null ? null : uuid(data.next_cursor) };
  },
  async issue(materialId: string, payload: AiIssuePayload) {
    const data = record(await request(`/materials/${uuid(materialId)}/ai-service-credentials`, "POST", payload));
    const result = credential(data.credential, materialId), available = boolean(data.secret_available);
    if (Date.parse(result.expiresAt) - Date.parse(result.createdAt) !== payload.lifetime_seconds * 1000) throw new Error("Invalid issued credential lifetime");
    if (available) {
      const token = string(data.token);
      if (!token.startsWith(`reawote_ai_${result.id}.`) || !/^[A-Za-z0-9_-]{43}$/.test(token.slice(`reawote_ai_${result.id}.`.length))) throw new Error("Invalid one-time credential response");
      return { credential: result, token };
    }
    if (data.token !== null) throw new Error("Unexpected recovered secret");
    return { credential: result, token: null };
  },
  async revoke(materialId: string, credentialId: string, payload: AiRevokePayload) {
    const result = credential(await request(`/materials/${uuid(materialId)}/ai-service-credentials/${uuid(credentialId)}/revoke`, "POST", payload), materialId);
    if (result.id !== credentialId || !result.revokedAt) throw new Error("Invalid credential revocation response"); return result;
  },
};
