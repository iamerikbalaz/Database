import { createHash, webcrypto } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
import { publicationBatchDto, publicationId, publicationPreviewDto } from "../test/publicationFixtures";
import { materialDto } from "../test/materialFixtures";
import { publicationBatchFromDto, publicationClient, publicationPreviewFromDto } from "./publicationClient";

afterEach(() => vi.unstubAllGlobals());
it("binds a complete preview to exactly the selected materials and approval hashes", () => {
  expect(publicationPreviewFromDto(publicationPreviewDto(), [materialDto.id]).items[0].row?.widthCm).toBe("12.5");
  expect(() => publicationPreviewFromDto(publicationPreviewDto(), [publicationId])).toThrow();
});
it.each(["duplicate", "hash", "name", "revision", "warning", "missing", "blocked", "count"])("rejects inconsistent preview %s", (defect) => {
  const value = publicationPreviewDto();
  if (defect === "duplicate") value.items.push(value.items[0]);
  if (defect === "hash") value.preview_hash = "invalid";
  if (defect === "name") value.items[0].row.name = "Other material";
  if (defect === "revision") value.items[0].row.revision_hash = "f".repeat(64);
  if (defect === "warning") value.items[0].warnings.push({ material_id: publicationId, code: "CONTENT_TAGS_EMPTY", fields: [] });
  if (defect === "missing") value.items = [];
  if (defect === "blocked") value.items[0].errors = ["CONTENT_APPROVAL_REQUIRED"];
  if (defect === "count") value.items = Array(101).fill(value.items[0]);
  expect(() => publicationPreviewFromDto(value, [materialDto.id])).toThrow();
});
it.each(["status", "count", "ordinal", "proof", "warning", "ack", "duplicate", "dimension"])("rejects inconsistent batch %s", (defect) => {
  const value = publicationBatchDto();
  if (defect === "status") value.status = "PUBLISHED";
  if (defect === "count") value.row_count = 2;
  if (defect === "ordinal") value.items[0].ordinal = 2;
  if (defect === "proof") value.items[0].metadata_snapshot_id = "missing";
  if (defect === "warning") value.warnings.push({ material_id: publicationId, code: "CONTENT_TAGS_EMPTY", fields: [] });
  if (defect === "ack") value.warnings.push({ material_id: materialDto.id, code: "CONTENT_TAGS_EMPTY", fields: [] });
  if (defect === "duplicate") value.items.push(value.items[0]);
  if (defect === "dimension") value.items[0].row.width_cm = "NaN";
  expect(() => publicationBatchFromDto(value)).toThrow();
});
it("sends exact saved requests and rejects mismatched returned snapshots", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(publicationBatchDto()))).mockResolvedValueOnce(new Response(JSON.stringify({ ...publicationBatchDto(), snapshot_hash: "f".repeat(64) })));
  vi.stubGlobal("fetch", fetcher);
  const payload = { material_ids: [materialDto.id], idempotency_key: publicationId, expected_preview_hash: "c".repeat(64), reason: "Prepare synthetic batch", warnings_acknowledged: false };
  expect((await publicationClient.create(payload)).id).toBe(publicationId);
  expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("/publication-batches"), expect.objectContaining({ credentials: "same-origin", cache: "no-store", method: "POST", body: JSON.stringify(payload) }));
  await expect(publicationClient.create(payload)).rejects.toThrow();
});
it("rejects history with a repeating or unrelated cursor", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [publicationBatchDto()], next_cursor: publicationId }))));
  await expect(publicationClient.history(publicationId)).rejects.toThrow();
});
const csv = new TextEncoder().encode('\ufeffidentity_name;name;description;credits;dimension;brand_identifier;categories;color;tags\r\nfixture;Surface;Reviewed;12;12.5x34 cm;brand;Stone;#A1B2C3;matte\r\n');
const digest = createHash("sha256").update(csv).digest("hex");
it("downloads only bounded bytes matching the saved digest and CSV format", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const fetcher = vi.fn().mockResolvedValue(new Response(csv, { headers: { "Content-Type": "text/csv; charset=utf-8", "Content-Length": String(csv.length), "X-Content-SHA256": digest } }));
  vi.stubGlobal("fetch", fetcher);
  const blob = await publicationClient.csv({ id: publicationId, csvSha256: digest }, new AbortController().signal);
  expect(blob.size).toBe(csv.length);
  expect(fetcher).toHaveBeenCalledWith(expect.stringContaining(`/publication-batches/${publicationId}/csv`), expect.objectContaining({ credentials: "same-origin", cache: "no-store" }));
});
it.each(["bytes", "hash", "type", "size", "length", "format", "abort"])("refuses download with invalid %s", async (defect) => {
  vi.stubGlobal("crypto", webcrypto);
  const body = defect === "format" ? new TextEncoder().encode("Not a publication CSV") : csv.slice();
  if (defect === "bytes") body[body.length - 1] = 0;
  const expected = defect === "format" ? createHash("sha256").update(body).digest("hex") : digest;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { headers: {
    "Content-Type": defect === "type" ? "text/html" : "text/csv",
    "Content-Length": defect === "size" ? String(33 * 1024 ** 2) : defect === "length" ? "1" : String(body.length),
    "X-Content-SHA256": defect === "hash" ? "a".repeat(64) : expected,
  } })));
  const controller = new AbortController(); if (defect === "abort") controller.abort();
  await expect(publicationClient.csv({ id: publicationId, csvSha256: expected }, controller.signal)).rejects.toThrow();
});
