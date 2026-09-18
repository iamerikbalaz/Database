import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";

export const accountSecurityLabels = {
  FIRST_ADMIN_PROVISIONED: "First administrator access issued",
  HOST_ACCESS_RECOVERED: "Access recovered from the host",
  ADMIN_ACCESS_PROVISIONED: "Temporary access issued by an administrator",
  ADMIN_ACCESS_RESET: "Access reset by an administrator",
  SELF_PASSWORD_CHANGED: "Password changed by the account owner",
};
export type AccountSecurityAction = keyof typeof accountSecurityLabels;
function timestamp(input: unknown) {
  const value = string(input);
  if (!/T.*(?:Z|[+-]\d\d:\d\d)$/.test(value) || !Number.isFinite(Date.parse(value))) throw new Error("Invalid security history date");
  return value;
}
function event(input: unknown, userId: string) {
  const value = record(input), action = string(value.action) as AccountSecurityAction;
  if (uuid(value.user_id) !== userId || !Object.keys(accountSecurityLabels).includes(action)
      || typeof value.version !== "number" || !Number.isSafeInteger(value.version) || value.version < 1 || value.version > 2147483647) throw new Error("Invalid security history event");
  const actorId = value.actor_id === null ? null : uuid(value.actor_id), requiresChange = boolean(value.requires_password_change);
  const host = action === "FIRST_ADMIN_PROVISIONED" || action === "HOST_ACCESS_RECOVERED", self = action === "SELF_PASSWORD_CHANGED";
  if (host ? actorId !== null : !actorId || (actorId === userId) !== self) throw new Error("Invalid security history actor");
  if (requiresChange === self || action === "FIRST_ADMIN_PROVISIONED" && value.version !== 1) throw new Error("Invalid security history outcome");
  return { id: uuid(value.id), userId, actorId, action, version: value.version, requiresChange,
    credentialChangedAt: timestamp(value.credential_changed_at), createdAt: timestamp(value.created_at) };
}
export function accountSecurityPage(input: unknown, userId: string, after: string | null = null) {
  const value = record(input); userId = uuid(userId);
  if (uuid(value.user_id) !== userId || !Array.isArray(value.items) || value.items.length > 20) throw new Error("Invalid security history page");
  const items = value.items.map((item) => event(item, userId)), nextCursor = value.next_cursor === null ? null : uuid(value.next_cursor);
  if (new Set(items.map((item) => item.id)).size !== items.length || items.some((item, index) => item.id === after || index > 0 && items[index - 1].version !== item.version + 1)
      || nextCursor && (items.length !== 20 || nextCursor !== items.at(-1)?.id || nextCursor === after)) throw new Error("Invalid security history ordering");
  return { items, nextCursor };
}
export const accountSecurityClient = {
  async history(userId: string, after: string | null = null) {
    return accountSecurityPage(await request(`/internal-users/${uuid(userId)}/security-history${after ? `?after=${uuid(after)}` : ""}`), userId, after);
  },
};
