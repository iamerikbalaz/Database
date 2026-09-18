import { afterEach, expect, it, vi } from "vitest";
import { catalogClient } from "./catalogClient";
import { contentReviewClient } from "./contentReviewClient";
import { identityClient } from "./identityClient";
import { reviewClient } from "./reviewClient";
import { materialDto, processorDto } from "../test/materialFixtures";

const id = "10000000-0000-4000-8000-000000000001", cursor = "a0000000-0000-4000-8000-000000000002";
const common = { id, actor_id: processorDto.id, created_at: "2026-09-18T08:00:00Z" };
const content = { material_id: materialDto.id, revision: 1, description: "Synthetic", credits: 0, tags: [], categories: [], collections: [], content_status: "MANUAL_DRAFT" };
const context = { ...materialDto, material_id: materialDto.id };
const cases = [
  { name: "audit", read: reviewClient.audit, row: { ...common, event_type: "REOPENED", details: {} }, wrap: (rows: unknown[]) => rows },
  { name: "identity-operations", read: identityClient.operations, row: { ...common, status: "COMPLETED", reason: "Synthetic", source_context: context, target_context: context, result: null }, wrap: (rows: unknown[]) => ({ mutations_enabled: false, operations: rows }) },
  { name: "content-history", read: catalogClient.history, row: { ...common, revision: 1, reason: "Synthetic", snapshot: content }, wrap: (rows: unknown[]) => rows },
  { name: "content-approvals", read: contentReviewClient.history, row: { ...common, content_revision: 1, context_hash: "a".repeat(64), note: null, warnings_acknowledged: false,
    snapshot: { schema_version: 1, content, brand: { name: "Synthetic", brand_identifier: "synthetic" }, material: context, source_review: { generation: 0, revision_hash: null } } }, wrap: (rows: unknown[]) => rows },
] as const;
const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
afterEach(() => { vi.unstubAllGlobals(); });

it.each(cases)("uses an explicit validated GET cursor for $name", async ({ name, read, row, wrap }) => {
  const fetch = vi.fn<typeof globalThis.fetch>(async () => json(wrap([row]))); vi.stubGlobal("fetch", fetch);
  await read(materialDto.id);
  expect(fetch.mock.calls[0][0]).toBe(`/api/materials/${materialDto.id}/${name}`);
  await read(materialDto.id, cursor);
  expect(fetch.mock.calls[1][0]).toBe(`/api/materials/${materialDto.id}/${name}?after=${cursor}`);
  expect(fetch.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
  await expect(read(materialDto.id, "../invalid")).rejects.toThrow();
  expect(fetch).toHaveBeenCalledTimes(2);
});

it.each(cases)("rejects oversized, duplicate and cursor-repeating $name pages", async ({ read, row, wrap }) => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  for (const rows of [Array.from({ length: 101 }, () => row), [row, row], [{ ...row, id: cursor.toUpperCase() }]]) {
    fetch.mockResolvedValueOnce(json(wrap(rows)));
    await expect(read(materialDto.id, cursor)).rejects.toThrow();
  }
});

it("rejects content and approval snapshots belonging to another material or revision", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  const draft = cases[2], approval = cases[3];
  for (const changed of [{ ...content, material_id: cursor }, { ...content, revision: 2 }]) {
    fetch.mockResolvedValueOnce(json([{ ...draft.row, snapshot: changed }]));
    await expect(draft.read(materialDto.id)).rejects.toThrow();
    fetch.mockResolvedValueOnce(json([{ ...approval.row, snapshot: { ...approval.row.snapshot, content: changed } }]));
    await expect(approval.read(materialDto.id)).rejects.toThrow();
  }
});

it("rejects identity history with a different source or target material", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  const identity = cases[1];
  for (const field of ["source_context", "target_context"]) {
    fetch.mockResolvedValueOnce(json(identity.wrap([{ ...identity.row, [field]: { ...context, material_id: cursor } }])));
    await expect(identity.read(materialDto.id)).rejects.toThrow();
  }
});
