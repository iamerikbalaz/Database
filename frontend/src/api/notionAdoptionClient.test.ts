import { afterEach, expect, it, vi } from "vitest";
import { adoptionRequest, notionAdoptionClient, notionAdoptionResult } from "./notionAdoptionClient";
import { notionComparison } from "./notionClient";
import { notionComparisonDto, notionCompanyId, notionId } from "../test/notionFixtures";
import { notionAdoptionDto } from "../test/notionAdoptionFixtures";
import { processorDto } from "../test/materialFixtures";
import { setSessionToken } from "../auth/sessionTransport";

const comparison = () => notionComparison(notionComparisonDto(), notionCompanyId, notionId, ["country", "name", "website"]);
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
it("sends exact selected comparison bindings with CSRF and recovers by a read-only request", async () => {
  const reviewed = comparison(), body = adoptionRequest(reviewed, ["name", "country"], " Reviewed values ");
  const fetch = vi.fn(async () => new Response(JSON.stringify(notionAdoptionDto(body)))); vi.stubGlobal("fetch", fetch); setSessionToken("s".repeat(43));
  const saved = await notionAdoptionClient.adopt(reviewed, processorDto.id, body);
  expect(saved.changed).toEqual(["country", "name"]); expect(saved.after.country).toBeNull();
  expect(fetch.mock.calls[0]).toMatchObject([`/api/companies/${notionCompanyId}/notion-adopt`, { method: "POST", cache: "no-store", credentials: "same-origin", headers: { "X-CSRF-Token": "s".repeat(43) }, body: JSON.stringify(body) }]);
  expect(await notionAdoptionClient.recover(reviewed, processorDto.id, body)).toEqual(saved);
  expect(fetch.mock.calls[1]).toMatchObject([`/api/companies/${notionCompanyId}/notion-adoptions/${body.request_key}`, { method: "GET", cache: "no-store" }]);
});
it.each(["key", "company", "actor", "action", "reason", "before", "after", "unselected-before", "extra-change", "link", "page", "source", "database", "mapping", "observation", "time", "selection", "raw"])("rejects unrelated adoption evidence: %s", (kind) => {
  const reviewed = comparison(), body = adoptionRequest(reviewed, ["country", "name"], "Reviewed values"), dto = notionAdoptionDto(body);
  if (kind === "key") dto.request_key = crypto.randomUUID();
  if (kind === "company") dto.event.company_id = crypto.randomUUID();
  if (kind === "actor") dto.event.actor_id = crypto.randomUUID();
  if (kind === "action") dto.event.action = "UPDATED";
  if (kind === "reason") dto.event.reason = "Changed reason";
  if (kind === "before") dto.event.before.country = "US";
  if (kind === "after") dto.event.after.country = "US";
  if (kind === "unselected-before") { dto.event.before.website = "https://different.invalid/"; dto.event.after.website = "https://different.invalid/"; }
  if (kind === "extra-change") dto.event.after.is_active = false;
  if (kind === "link") dto.event.before.notion_page_id = crypto.randomUUID();
  if (kind === "page") dto.event.source.page_id = crypto.randomUUID();
  if (kind === "source") dto.event.source.data_source_id = crypto.randomUUID();
  if (kind === "database") dto.event.source.database_id = crypto.randomUUID();
  if (kind === "mapping") dto.event.source.mapping_sha256 = "d".repeat(64);
  if (kind === "observation") dto.event.source.observation_sha256 = "d".repeat(64);
  if (kind === "time") dto.event.source.last_edited_time = "2026-09-18T14:00:00Z";
  if (kind === "selection") dto.event.source.selected_fields = ["name"];
  if (kind === "raw") Object.assign(dto.event.source, { raw: "unmapped" });
  expect(() => notionAdoptionResult(dto, reviewed, processorDto.id, body)).toThrow();
});
it.each(["empty", "duplicate", "unchanged", "reason", "oversize", "unsafe", "zero"])("rejects an unreviewable command before HTTP: %s", (kind) => {
  const fields = kind === "empty" ? [] : kind === "duplicate" ? ["name", "name"] as const : kind === "unchanged" ? ["website"] as const : ["name"] as const;
  const reason = kind === "reason" ? " " : kind === "oversize" ? "a".repeat(2001) : kind === "unsafe" ? "bad\x00reason" : "Reviewed";
  expect(() => adoptionRequest(comparison(), [...fields], reason, kind === "zero" ? "00000000-0000-0000-0000-000000000000" : crypto.randomUUID())).toThrow();
});
it("never sends a request detached from its reviewed local snapshot", async () => {
  const reviewed = comparison(), body = adoptionRequest(reviewed, ["name"], "Reviewed"), fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  body.expected_local_sha256 = "b".repeat(64);
  await expect(notionAdoptionClient.adopt(reviewed, processorDto.id, body)).rejects.toThrow(); expect(fetch).not.toHaveBeenCalled();
});
