import { afterEach, expect, it, vi } from "vitest";
import { accountSecurityClient, accountSecurityLabels, accountSecurityPage, type AccountSecurityAction } from "./accountSecurityClient";
import { securityEventDto, securityHistoryDto, securityUserId } from "../test/accountSecurityFixtures";

afterEach(() => vi.unstubAllGlobals());
it.each(Object.keys(accountSecurityLabels) as AccountSecurityAction[])("accepts only correctly bound %s history", (action) => {
  const page = securityHistoryDto(1); page.items[0] = securityEventDto(action);
  expect(accountSecurityPage(page, securityUserId).items[0].action).toBe(action);
});
it("uses an explicit, non-cached bounded history request", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(securityHistoryDto()))); vi.stubGlobal("fetch", fetch);
  const cursor = crypto.randomUUID(); await accountSecurityClient.history(securityUserId, cursor);
  expect(fetch.mock.calls[0][0]).toBe(`/api/internal-users/${securityUserId}/security-history?after=${cursor}`);
  expect(fetch.mock.calls[0][1]).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
});
it.each(["user", "event-user", "actor", "missing-actor", "self-admin", "action", "inherited-action", "outcome", "version", "bootstrap", "date", "changed-date", "order", "gap", "duplicate", "cursor", "too-many"])("rejects malformed security history: %s", (change) => {
  const dto = securityHistoryDto(), item = dto.items[0];
  if (change === "user") dto.user_id = crypto.randomUUID();
  if (change === "event-user") item.user_id = crypto.randomUUID();
  if (change === "actor") item.actor_id = "invalid";
  if (change === "missing-actor") item.actor_id = null;
  if (change === "self-admin") item.actor_id = securityUserId;
  if (change === "action") item.action = "UNKNOWN";
  if (change === "inherited-action") item.action = "constructor";
  if (change === "outcome") item.requires_password_change = false;
  if (change === "version") item.version = 1.5;
  if (change === "bootstrap") dto.items[0] = securityEventDto("FIRST_ADMIN_PROVISIONED", 2);
  if (change === "date") item.created_at = "not-a-date";
  if (change === "changed-date") item.credential_changed_at = "2026-09-18T18:00:00";
  if (change === "order") dto.items.reverse();
  if (change === "gap") item.version = 3;
  if (change === "duplicate") dto.items.push(item);
  if (change === "cursor") dto.next_cursor = item.id;
  if (change === "too-many") dto.items = securityHistoryDto(21).items;
  expect(() => accountSecurityPage(dto, securityUserId)).toThrow();
});
it.each(["FIRST_ADMIN_PROVISIONED", "HOST_ACCESS_RECOVERED", "SELF_PASSWORD_CHANGED"] as const)("rejects a fabricated actor for %s", (action) => {
  const dto = securityHistoryDto(1); dto.items[0] = securityEventDto(action);
  dto.items[0].actor_id = crypto.randomUUID(); expect(() => accountSecurityPage(dto, securityUserId)).toThrow();
});
it("requires a full page's final event and rejects cursor loops", () => {
  const dto = securityHistoryDto(20); dto.next_cursor = dto.items[19].id;
  expect(accountSecurityPage(dto, securityUserId).nextCursor).toBe(dto.next_cursor);
  expect(() => accountSecurityPage(dto, securityUserId, dto.next_cursor)).toThrow();
});
