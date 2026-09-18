import { afterEach, expect, it, vi } from "vitest";
import { notionClient, notionComparison, notionConfiguration, notionPageId } from "./notionClient";
import { setSessionToken } from "../auth/sessionTransport";
import { notionConfig, notionComparisonDto, notionCompanyId, notionId } from "../test/notionFixtures";

afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
it("reads configuration, then sends only an exact linked-page selection with CSRF", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(notionConfig())))
    .mockResolvedValueOnce(new Response(JSON.stringify(notionComparisonDto())));
  vi.stubGlobal("fetch", fetch); setSessionToken("s".repeat(43));
  const config = await notionClient.configuration();
  const comparison = await notionClient.compare(notionCompanyId, notionId.replaceAll("-", ""), config.fields);
  expect(comparison.rows[1].observed).toBe("Synthetic česká company");
  expect(fetch.mock.calls[0][0]).toBe("/api/integrations/notion");
  expect(fetch.mock.calls[1][0]).toBe(`/api/companies/${notionCompanyId}/notion-preview`);
  expect(fetch.mock.calls[1][1]).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store", headers: { "X-CSRF-Token": "s".repeat(43) } });
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ expected_page_id: notionId });
});
it.each([null, "", "0".repeat(32), "../page", "https://example.invalid", `{${notionId}}`, ` ${notionId}`])("rejects invalid explicit link %s", (input) => {
  expect(notionPageId(input)).toBeNull();
});
it("accepts modern UUID versions and compact uppercase links without discovery", () => {
  expect(notionPageId(notionId.replaceAll("-", "").toUpperCase())).toBe(notionId);
});
it.each(["wrong-company", "wrong-page", "source", "database", "mapping-hash", "observation-hash", "local-hash", "timestamp", "direction", "extra-field", "duplicate-field", "reordered-fields", "missing-name", "wrong-change", "null-name", "oversize", "unsafe-text", "invalid-value"])("rejects malformed or unrelated comparison: %s", (kind) => {
  const dto = notionComparisonDto();
  if (kind === "wrong-company") dto.company_id = crypto.randomUUID();
  if (kind === "wrong-page") dto.source.page_id = crypto.randomUUID();
  if (kind === "source") dto.source.data_source_id = "0".repeat(36);
  if (kind === "database") dto.source.database_id = "00000000-0000-0000-0000-000000000000";
  if (kind === "mapping-hash") dto.source.mapping_sha256 = "bad";
  if (kind === "observation-hash") dto.source.observation_sha256 = "A".repeat(64);
  if (kind === "local-hash") dto.local_sha256 = "a".repeat(63);
  if (kind === "timestamp") dto.source.last_edited_time = "2026-09-18T12:00:00";
  if (kind === "direction") dto.direction = "WRITE";
  if (kind === "extra-field") dto.fields[0].field = "role";
  if (kind === "duplicate-field") dto.fields.push(dto.fields[0]);
  if (kind === "reordered-fields") dto.fields.reverse();
  if (kind === "missing-name") dto.fields.splice(1, 1);
  if (kind === "wrong-change") dto.fields[0].changed = false;
  if (kind === "null-name") dto.fields[1].observed = null;
  if (kind === "oversize") dto.fields[1].observed = "x".repeat(256);
  if (kind === "unsafe-text") dto.fields[1].observed = "unsafe\0text";
  if (kind === "invalid-value") Object.assign(dto.fields[1], { observed: { raw: "untrusted" } });
  expect(() => notionComparison(dto, notionCompanyId, notionId, ["country", "name", "website"])).toThrow();
});
it.each([{ enabled: "true" }, { direction: "WRITE" }, { resource: "PROJECT" }, { mapped_fields: [] },
  { mapped_fields: ["name", "name"] }, { mapped_fields: ["role", "name"] }, { mapped_fields: ["name", "country"] }])("rejects invalid configuration %j", (change) => {
  expect(() => notionConfiguration({ ...notionConfig(), ...change })).toThrow();
});
it("accepts disabled configuration without a field mapping", () => {
  expect(notionConfiguration({ ...notionConfig(), enabled: false, mapped_fields: [] })).toEqual({ enabled: false, fields: [] });
});
it("rejects invalid selection before any HTTP request", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  await expect(notionClient.compare(notionCompanyId, "not-an-id", ["name"])).rejects.toThrow(); expect(fetch).not.toHaveBeenCalled();
});
