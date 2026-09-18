import { afterEach, expect, it, vi } from "vitest";
import { stagingClient, stagingPreviewFromDto, stagingJobFromDto, stagingDispatchFromDto, stagingTransferFromDto } from "./stagingClient";
import { publicationBatchFromDto } from "./publicationClient";
import { publicationBatchDto, publicationId } from "../test/publicationFixtures";
import { stagingPreviewDto, stagingJobDto, stagingDispatchDto, stagingTransferDto, selection, stagingId, stagingDispatchId, stagingHash, packageId } from "../test/stagingFixtures";
import { setSessionToken } from "../auth/sessionTransport";

afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
const batch = () => publicationBatchFromDto(publicationBatchDto());
it("binds preview to exact CSV selection and treats staging as an internal contract", () => {
  const value = stagingPreviewFromDto(stagingPreviewDto(), batch(), selection());
  expect(value.jobId).toBe(stagingId); expect(value.objectCount).toBe(3); expect(value.materials[0].executionId).toBe(packageId);
});
it.each([
  { importer_compatible: true }, { layout: "ONLINE_READY" }, { batch_id: stagingId }, { job_id: publicationId },
  { materials: [] }, { object_count: 2 }, { total_bytes: 2 }, { transfer_enabled: "true" }, { has_more_objects: true },
  { bucket_name: "https://untrusted.invalid" }, { staging_prefix: "../outside" },
])("rejects mismatched preview %j", (change) => {
  expect(() => stagingPreviewFromDto({ ...stagingPreviewDto(), ...change }, batch(), selection())).toThrow();
});
it.each(["execution_id", "packaging_proof_sha256", "batch_item_sha256", "revision_hash", "content_context_hash"])("rejects changed %s", (key) => {
  const dto = stagingPreviewDto(); const bound = dto.materials[0] as Record<string, unknown>; bound[key] = key === "execution_id" ? stagingId : "0".repeat(64);
  expect(() => stagingPreviewFromDto(dto, batch(), selection())).toThrow();
});
it.each(["../escaped", "materials//file", "a\\b", "č".repeat(129), "a/".repeat(17) + "z"])("rejects unsafe object path %s", (path) => {
  const dto = stagingPreviewDto(); dto.objects_preview[0].relative_path = path;
  expect(() => stagingPreviewFromDto(dto, batch(), selection())).toThrow();
});
it.each(["RESERVED", "RUNNING", "RECOVERY_REQUIRED", "STAGED_VERIFIED", "CLOSED"])("decodes consistent %s history", (status) => {
  expect(stagingJobFromDto(stagingJobDto(status)).status).toBe(status);
});
it.each([
  { status: "PUBLISHED" }, { status: "RUNNING" }, { last_result_id: stagingId }, { last_dispatch_id: stagingDispatchId }, { close: {} }, { material_count: 2 },
])("rejects inconsistent job %j", (change) => { expect(() => stagingJobFromDto({ ...stagingJobDto(), ...change })).toThrow(); });
it("keeps exact generation strings above JavaScript's safe integer range", () => {
  expect(stagingTransferFromDto(stagingTransferDto(), stagingJobFromDto(stagingJobDto())).observation?.receipt?.generation).toBe("9007199254740993");
});
it.each([true, 1, "0", "01", "9223372036854775808", "1e3"])("rejects forged receipt generation %s", (generation) => {
  const dto = stagingTransferDto(); const receipt = dto.observation.receipt as Record<string, unknown>; receipt.generation = generation;
  expect(() => stagingTransferFromDto(dto, stagingJobFromDto(stagingJobDto()))).toThrow();
});
it.each(["job_id", "binding_sha256", "relative_path", "size", "sha256"])("rejects mismatched receipt %s", (key) => {
  const dto = stagingTransferDto(); (dto.observation.receipt.spec as Record<string, unknown>)[key] = key === "size" ? true : key === "job_id" ? publicationId : "wrong";
  expect(() => stagingTransferFromDto(dto, stagingJobFromDto(stagingJobDto()))).toThrow();
});
it("rejects a verified result without completion evidence and an invalid dispatch chain", () => {
  expect(() => stagingDispatchFromDto({ ...stagingDispatchDto(), action: "RECONCILE" })).toThrow();
  const dto = stagingDispatchDto(); (dto.result as Record<string, unknown>).completion_observation_id = null;
  expect(() => stagingDispatchFromDto(dto)).toThrow();
});
it("sends authenticated explicit commands with the exact frozen body", async () => {
  setSessionToken("s".repeat(43));
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(stagingJobDto("STAGED_VERIFIED")))); vi.stubGlobal("fetch", fetch);
  const body = { idempotency_key: publicationId, expected_plan_sha256: stagingHash, expected_last_dispatch_id: null, reason: "Reviewed synthetic staging" };
  expect((await stagingClient.command(stagingJobFromDto(stagingJobDto()), "run", body)).status).toBe("STAGED_VERIFIED");
  expect(fetch).toHaveBeenCalledWith(`/api/publication-staging-jobs/${stagingId}/run`, expect.objectContaining({ method: "POST", credentials: "same-origin", cache: "no-store",
    body: JSON.stringify(body), headers: expect.objectContaining({ "X-CSRF-Token": "s".repeat(43) }) }));
});
it("rejects gaps in paged history and invalid cursors", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [{ ...stagingDispatchDto(), ordinal: 3, previous_dispatch_id: stagingId, action: "RECONCILE" }], next_cursor: 3 }))));
  await expect(stagingClient.dispatches(stagingJobFromDto(stagingJobDto()), 1)).rejects.toThrow();
});
it("rejects an unrelated returned reservation", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...stagingJobDto(), batch_id: stagingId }))));
  await expect(stagingClient.reserve({ ...selection(), batch_id: publicationId, idempotency_key: stagingDispatchId, expected_plan_sha256: stagingHash, reason: "Reviewed synthetic staging" })).rejects.toThrow();
});
