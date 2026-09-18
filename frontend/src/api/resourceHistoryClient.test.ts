import { afterEach, expect, it, vi } from "vitest";
import { resourceHistoryClient, resourceHistoryPage, type ResourceKind } from "./resourceHistoryClient";
import { historyResourceId, resourceHistoryDto } from "../test/resourceHistoryFixtures";

const kinds: ResourceKind[] = ["BRAND", "PROJECT", "USER", "MATERIAL"];
afterEach(() => vi.unstubAllGlobals());
it.each(kinds)("reads bounded, target-bound %s history", async (kind) => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(resourceHistoryDto(kind)))); vi.stubGlobal("fetch", fetch);
  const cursor = crypto.randomUUID(), page = await resourceHistoryClient.history(kind, historyResourceId, cursor);
  expect(page.items.map((item) => item.version)).toEqual([2, 1]); expect(page.items[0].changed).toHaveLength(1); expect(page.items[1].before).toBeNull();
  const segment = { BRAND: "brands", PROJECT: "projects", USER: "internal-users", MATERIAL: "materials" }[kind];
  expect(fetch.mock.calls[0][0]).toBe(`/api/${segment}/${historyResourceId}/history?after=${cursor}`);
  expect(fetch.mock.calls[0][1]).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
});
for (const kind of kinds) {
  it.each(["kind", "id", "event-kind", "event-id", "actor", "snapshot", "extra-before", "extra-after", "nested", "version", "creation", "action", "hash", "date", "noop", "order", "gap", "duplicate", "cursor", "many"])(`rejects malformed ${kind} history: %s`, (change) => {
    const dto = resourceHistoryDto(kind), event = dto.items[0];
    if (change === "kind") dto.resource_kind = "OTHER";
    if (change === "id") dto.resource_id = crypto.randomUUID();
    if (change === "event-kind") event.resource_kind = "OTHER";
    if (change === "event-id") event.resource_id = crypto.randomUUID();
    if (change === "actor") event.actor_id = "invalid";
    if (change === "snapshot") event.after.id = crypto.randomUUID();
    if (change === "extra-before") Object.assign(event.before, { raw: "Unmapped" });
    if (change === "extra-after") event.after.secret = "Unmapped";
    if (change === "nested") event.after[Object.keys(event.after)[1]] = { raw: "Nested" };
    if (change === "version") event.version = 1.5;
    if (change === "creation") event.action = "CREATED";
    if (change === "action") event.action = "DELETE";
    if (change === "hash") event.before_sha256 = "bad";
    if (change === "date") event.created_at = "not-a-date";
    if (change === "noop") event.before = event.after;
    if (change === "order") dto.items.reverse();
    if (change === "gap") event.version = 3;
    if (change === "duplicate") dto.items.push(event);
    if (change === "cursor") dto.next_cursor = event.id;
    if (change === "many") dto.items = resourceHistoryDto(kind, 21).items;
    expect(() => resourceHistoryPage(dto, kind, historyResourceId)).toThrow();
  });
}
it("only follows a full page's final event and rejects a repeated cursor", () => {
  const dto = resourceHistoryDto("PROJECT", 20); dto.next_cursor = dto.items[19].id;
  expect(resourceHistoryPage(dto, "PROJECT", historyResourceId).nextCursor).toBe(dto.next_cursor);
  expect(() => resourceHistoryPage(dto, "PROJECT", historyResourceId, dto.next_cursor)).toThrow();
  dto.next_cursor = crypto.randomUUID(); expect(() => resourceHistoryPage(dto, "PROJECT", historyResourceId)).toThrow();
});
