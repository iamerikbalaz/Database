import { afterEach, expect, it, vi } from "vitest";
import { archiveDetail, archivePage, archivePreview, lifecycleEvent, lifecyclePage, lifecycleRequest, materialArchiveClient } from "./materialArchiveClient";
import { archiveActorId, archiveDetailDto, archiveMaterialId, archivePreviewDto, lifecycleEventDto } from "../test/materialArchiveFixtures";

afterEach(() => vi.unstubAllGlobals());
const preview = () => archivePreview(archivePreviewDto(), archiveMaterialId, "ARCHIVE");
function response(body: unknown) { return new Response(JSON.stringify(body)); }

it.each([0, 1, 2, 3])("parses lifecycle version %s and scoped identity", (version) => {
  expect(archiveDetail(archiveDetailDto(version), archiveMaterialId)).toMatchObject({ id: archiveMaterialId, version, isArchived: version % 2 === 1 });
});
it.each(["target", "version", "parity", "date", "initial-date", "publication", "readiness", "extra-field"])("rejects contradictory archive state: %s", (change) => {
  const dto = archiveDetailDto(1);
  if (change === "target") dto.material.id = crypto.randomUUID();
  if (change === "version") dto.version = 1.5;
  if (change === "parity") dto.is_archived = false;
  if (change === "date") dto.changed_at = "not-a-date";
  if (change === "initial-date") { dto.version = 0; dto.is_archived = false; }
  if (change === "publication") dto.material.is_published = true;
  if (change === "readiness") dto.material.workflow_status = "DONE";
  if (change === "extra-field") dto.material.unexpected = "not accepted";
  expect(() => archiveDetail(dto, archiveMaterialId)).toThrow();
});
it.each(["action", "digest", "blocked", "eligibility"])("rejects invalid preview: %s", (change) => {
  const dto = archivePreviewDto();
  if (change === "action") dto.action = "RESTORE";
  if (change === "digest") dto.input_sha256 = "invalid";
  if (change === "blocked") dto.blocked_code = "MATERIAL_OPERATION_ACTIVE";
  if (change === "eligibility") dto.can_apply = false;
  expect(() => archivePreview(dto, archiveMaterialId, "ARCHIVE")).toThrow();
});
it("accepts an explicit blocked preview but forbids making a command from it", () => {
  const dto = archivePreviewDto(); dto.can_apply = false; dto.blocked_code = "MATERIAL_OPERATION_ACTIVE";
  const blocked = archivePreview(dto, archiveMaterialId, "ARCHIVE");
  expect(() => lifecycleRequest(blocked, "Explanation")).toThrow();
});
it.each(["", "  ", "bad\x00value", "x".repeat(2001)])("requires a bounded readable reason", (reason) => {
  expect(() => lifecycleRequest(preview(), reason)).toThrow();
});
it("requires a nonzero key and a version that can advance", () => {
  expect(() => lifecycleRequest(preview(), "Explanation", "00000000-0000-0000-0000-000000000000")).toThrow();
  expect(() => lifecycleRequest({ ...preview(), version: 2147483647 }, "Explanation")).toThrow();
});
it.each(["actor", "target", "request-key", "reason", "action", "version", "input-hash"])("rejects unrelated command evidence: %s", async (change) => {
  const dto = lifecycleEventDto(), body = lifecycleRequest(preview(), dto.reason, dto.request_key);
  if (change === "actor") dto.actor_id = crypto.randomUUID();
  if (change === "target") dto.material_id = crypto.randomUUID();
  if (change === "request-key") dto.request_key = crypto.randomUUID();
  if (change === "reason") dto.reason = "Another reason";
  if (change === "action") dto.action = "RESTORE";
  if (change === "version") dto.version = 3;
  if (change === "input-hash") dto.input_sha256 = "c".repeat(64);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ event: dto })));
  await expect(materialArchiveClient.command(preview(), archiveActorId, body)).rejects.toThrow();
});
it("sends one exact command and read-only recovery under the same actor and target", async () => {
  const dto = lifecycleEventDto(), body = lifecycleRequest(preview(), dto.reason, dto.request_key);
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(response({ event: dto }))); vi.stubGlobal("fetch", fetch);
  const saved = await materialArchiveClient.command(preview(), archiveActorId, body);
  expect(saved.version).toBe(1);
  expect(fetch.mock.calls[0][0]).toBe(`/api/material-archives/${archiveMaterialId}/commands`);
  expect(fetch.mock.calls[0][1]).toMatchObject({ method: "POST", body: JSON.stringify(body), cache: "no-store", credentials: "same-origin" });
  expect(await materialArchiveClient.recover(preview(), archiveActorId, body)).toEqual(saved);
  expect(fetch.mock.calls[1][0]).toBe(`/api/material-archives/${archiveMaterialId}/commands/${body.request_key}`);
  expect(fetch.mock.calls[1][1].method).toBe("GET");
  expect(fetch).toHaveBeenCalledTimes(2);
});
it("never sends a changed frozen command", async () => {
  const body = lifecycleRequest(preview(), "Explanation"), fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  await expect(materialArchiveClient.command(preview(), archiveActorId, { ...body, expected_version: 2 })).rejects.toThrow();
  expect(fetch).not.toHaveBeenCalled();
});
it("does not retry a 503 command", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("{}", { status: 503 })); vi.stubGlobal("fetch", fetch);
  await expect(materialArchiveClient.command(preview(), archiveActorId, lifecycleRequest(preview(), "Explanation"))).rejects.toThrow();
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("checks archive list bounds, order and cursor", () => {
  const items = Array.from({ length: 20 }, (_, index) => archiveDetailDto(1, `77777777-7777-4777-8777-${String(index + 1).padStart(12, "0")}`));
  const dto = { items, next_cursor: items[19].material.id };
  expect(archivePage(dto).items).toHaveLength(20);
  expect(() => archivePage(dto, String(dto.next_cursor))).toThrow();
  expect(() => archivePage({ ...dto, items: [...items].reverse() })).toThrow();
  expect(() => archivePage({ items: [archiveDetailDto(2)], next_cursor: null })).toThrow();
  expect(() => archivePage({ items: [...items, items[0]], next_cursor: null })).toThrow();
});
it("requires scoped, consecutive lifecycle pages and a final full-page cursor", () => {
  const items = Array.from({ length: 20 }, (_, index) => lifecycleEventDto(20 - index));
  const dto = { material_id: archiveMaterialId, items, next_cursor: items[19].id };
  expect(lifecyclePage(dto, archiveMaterialId).items).toHaveLength(20);
  expect(() => lifecyclePage(dto, crypto.randomUUID())).toThrow();
  expect(() => lifecyclePage(dto, archiveMaterialId, dto.next_cursor)).toThrow();
  expect(() => lifecyclePage({ ...dto, items: items.slice(1) }, archiveMaterialId)).toThrow();
  expect(() => lifecyclePage({ ...dto, items: [...items].reverse() }, archiveMaterialId)).toThrow();
  expect(() => lifecycleEvent({ ...items[0], review_generation: Number.MAX_SAFE_INTEGER + 1 }, archiveMaterialId)).toThrow();
});
