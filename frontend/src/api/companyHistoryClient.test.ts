import { afterEach, expect, it, vi } from "vitest";
import { companyHistoryClient, companyHistoryPage } from "./companyHistoryClient";
import { companyHistoryDto } from "../test/companyHistoryFixtures";
import { notionCompanyId } from "../test/notionFixtures";

afterEach(() => vi.unstubAllGlobals());
it("loads a bounded explicit company history page", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(companyHistoryDto()))); vi.stubGlobal("fetch", fetch);
  const after = crypto.randomUUID(); const page = await companyHistoryClient.history(notionCompanyId, after);
  expect(page.items.map((item) => item.version)).toEqual([2, 1]); expect(page.items[0].changed).toEqual(["name"]);
  expect(page.items[1].before).toBeNull(); expect(fetch.mock.calls[0][0]).toBe(`/api/companies/${notionCompanyId}/history?after=${after}`);
  expect(fetch.mock.calls[0][1]).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
});
it.each(["company", "snapshot", "actor", "version", "ordering", "gap", "creation", "hash", "timestamp", "changed", "extra", "type", "duplicate", "cursor", "too-many"])("rejects invalid company history: %s", (change) => {
  const dto = companyHistoryDto();
  if (change === "company") dto.company_id = crypto.randomUUID();
  if (change === "snapshot") dto.items[0].after.id = crypto.randomUUID();
  if (change === "actor") dto.items[0].actor_id = "invalid";
  if (change === "version") dto.items[0].version = 1.5;
  if (change === "ordering") dto.items.reverse();
  if (change === "gap") dto.items[0].version = 3;
  if (change === "creation") dto.items[0].action = "CREATED";
  if (change === "hash") dto.items[0].after_sha256 = "bad";
  if (change === "timestamp") dto.items[0].created_at = "not-a-date";
  if (change === "changed") dto.items[0].before = dto.items[0].after;
  if (change === "extra") Object.assign(dto.items[0].before, { raw: "Unmapped property" });
  if (change === "type") Object.assign(dto.items[0].after, { is_active: "true" });
  if (change === "duplicate") dto.items.push(dto.items[0]);
  if (change === "cursor") dto.next_cursor = dto.items[1].id;
  if (change === "too-many") dto.items = companyHistoryDto(21).items;
  expect(() => companyHistoryPage(dto, notionCompanyId)).toThrow();
});
it("requires the next cursor to match a full page's last event", () => {
  const dto = companyHistoryDto(20); dto.next_cursor = dto.items[19].id;
  expect(companyHistoryPage(dto, notionCompanyId).nextCursor).toBe(dto.next_cursor);
  expect(() => companyHistoryPage(dto, notionCompanyId, dto.items[5].id)).toThrow();
  dto.next_cursor = crypto.randomUUID(); expect(() => companyHistoryPage(dto, notionCompanyId)).toThrow();
});
