import type { AccountSecurityAction } from "../api/accountSecurityClient";
export const securityUserId = "11111111-1111-4111-8111-111111111111";
export function securityEventDto(action: AccountSecurityAction = "ADMIN_ACCESS_RESET", version = 1, userId = securityUserId) {
  const host = action === "FIRST_ADMIN_PROVISIONED" || action === "HOST_ACCESS_RECOVERED";
  return { id: `44444444-4444-4444-8444-${String(version).padStart(12, "0")}`, user_id: userId,
    actor_id: host ? null : action === "SELF_PASSWORD_CHANGED" ? userId : "55555555-5555-4555-8555-555555555555",
    version, action: action as string, requires_password_change: action !== "SELF_PASSWORD_CHANGED",
    credential_changed_at: "2026-09-18T18:00:00Z", created_at: "2026-09-18T18:00:01Z" };
}
export function securityHistoryDto(count = 2, userId = securityUserId) {
  return { user_id: userId, items: Array.from({ length: count }, (_, index) => securityEventDto("ADMIN_ACCESS_RESET", count - index, userId)), next_cursor: null as string | null };
}
