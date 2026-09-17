import { afterEach, expect, it, vi } from "vitest";
import { importClient, importFailureMessage, importFindingMessage, parseImportInspection, parseImportPreview, parseImportResult, readImportFile, type ImportConfirmation } from "./importClient";
import { responseError } from "./errors";
import { importBatch, importColumns, importInspection, importMappings, importPreview, importRow } from "../test/importFixtures";
import { materialBrand, materialProject, processorDto } from "../test/materialFixtures";
import { setSessionToken } from "../auth/sessionTransport";

afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });

it("parses explicit worksheet selection, a bounded sample and literal mapping labels", () => {
  expect(parseImportInspection({ format: "XLSX", sheets: ["Materials"], requires_sheet: true })).toEqual({ format: "XLSX", sheets: ["Materials"], requiresSheet: true });
  const result = parseImportInspection({ ...importInspection, mapping_values: importMappings });
  expect(result.requiresSheet).toBe(false);
  if (!result.requiresSheet) { expect(result.headers).toEqual(Object.values(importColumns)); expect(result.mappingValues).toEqual(importMappings); }
});
it.each([
  { requires_sheet: true }, { headers: ["Duplicate", "Duplicate"] }, { row_count: 2001 }, { sample: Array(11).fill(importInspection.sample[0]) },
  { source_sha256: "invalid" }, { sample: [{ row: 2, values: ["Missing cells"] }] }, { mapping_values: { ...importMappings, project: Array(65).fill("P") } },
])("rejects malformed or unbounded inspection responses %#", (update) => {
  expect(() => parseImportInspection({ ...importInspection, ...update })).toThrow();
});
it("accepts a blocked preview without enabling confirmation or reflecting unknown finding codes", () => {
  const result = parseImportPreview({ ...importPreview, can_confirm: false, preview_hash: null,
    findings: [{ row: 2, field: "identity", code: "IMPORT_NUMBER_RESERVED" }] });
  expect(result.ready).toBe(false); expect(result.hash).toBeNull();
  expect(importFindingMessage(result.findings[0].code)).toContain("permanently reserved");
  expect(importFindingMessage("constructor")).toBe("This row needs correction before import.");
});
it.each([
  { can_confirm: false }, { preview_hash: null }, { row_count: 2 }, { findings: [{ row: 2, field: "identity", code: "IMPORT_NUMBER_RESERVED" }] },
  { snapshot: { ...importPreview.snapshot, initial_state: { ...importPreview.snapshot.initial_state, workflow_status: "DONE" } } },
  { snapshot: { ...importPreview.snapshot, rows: [{ ...importRow, project_id: "74000000-0000-4000-8000-000000000001" }] } },
  { snapshot: { ...importPreview.snapshot, rows: [importRow, importRow] }, row_count: 2 },
])("rejects inconsistent, duplicate or implicitly approved previews %#", (update) => {
  expect(() => parseImportPreview({ ...importPreview, ...update })).toThrow();
});
it.each([
  { rows: [] }, { snapshot: { ...importBatch.snapshot, source_sha256: "c".repeat(64) } },
  { rows: [{ ...importBatch.rows[0], publication_status: "PUBLISHED_CURRENT" }] },
  { rows: [importBatch.rows[0], importBatch.rows[0]], row_count: 2 },
])("rejects incomplete or inconsistent durable results %#", (update) => {
  expect(() => parseImportResult({ ...importBatch, ...update })).toThrow();
});
it("uses authenticated same-origin no-store transport and verifies the confirmed request key", async () => {
  const body: ImportConfirmation = { source: { format: "CSV", delimiter: ";", data: "eA==" }, columns: importColumns,
    links: { projects: { "Project A": materialProject.id }, brands: { "Brand A": materialBrand.id }, processors: { "Processor A": processorDto.id } },
    idempotency_key: importBatch.idempotency_key, expected_preview_hash: importBatch.preview_hash, acknowledge_unverified: true, reason: importBatch.reason };
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(importBatch)));
  vi.stubGlobal("fetch", fetch); setSessionToken("t".repeat(43));
  expect((await importClient.confirm(body)).id).toBe(importBatch.id);
  expect(fetch).toHaveBeenCalledWith("/api/material-imports/confirm", expect.objectContaining({ method: "POST", credentials: "same-origin", cache: "no-store",
    body: JSON.stringify(body), headers: expect.objectContaining({ "Content-Type": "application/json", "X-CSRF-Token": "t".repeat(43) }) }));
  fetch.mockResolvedValue(new Response(JSON.stringify({ ...importBatch, idempotency_key: "74000000-0000-4000-8000-000000000001" })));
  await expect(importClient.confirm(body)).rejects.toThrow("Mismatched import result");
});
it("checks history cursors against the actual last row", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [importBatch], next_after: processorDto.id }))));
  await expect(importClient.history()).rejects.toThrow("Invalid import cursor");
});
it("reads exact original bytes and rejects empty or oversized files before reading", async () => {
  const file = new File([new Uint8Array([0, 128, 255, 65])], "synthetic.xlsx");
  expect(await readImportFile(file)).toBe("AID/QQ==");
  await expect(readImportFile(new File([], "empty.csv"))).rejects.toThrow("nonempty");
  await expect(readImportFile({ size: 4 * 1024 ** 2 + 1 } as File)).rejects.toThrow("4 MiB");
});
it("shows fixed actionable source errors with validated coordinates and no reflected values", () => {
  const failure = responseError(422, { detail: { code: "IMPORT_FORMULA", row: 2, column: 3, input: "PRIVATE_SYNTHETIC_MARKER" } });
  expect(importFailureMessage(failure)).toBe("Row 2, column 3: Replace spreadsheet formulas or formula-prefixed values with reviewed literal values.");
  const unknown = responseError(422, { detail: { code: "PRIVATE_SYNTHETIC_MARKER", row: "PRIVATE_SYNTHETIC_MARKER", column: 1000 } });
  expect(importFailureMessage(unknown)).not.toContain("PRIVATE_SYNTHETIC_MARKER");
  expect(unknown.sourcePosition).toEqual({});
});
