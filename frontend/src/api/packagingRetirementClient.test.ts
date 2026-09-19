import { randomBytes, webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { packagingRetirementClient as api, retirementFromDto } from "./packagingRetirementClient";
import { setSessionToken } from "../auth/sessionTransport";
import { actorId, copyJob, dispatchId, materialId, removalAction, retirementDto, retirementId, retirementRequest } from "../test/packagingRetirementFixtures";

const json = (body: unknown) => new Response(JSON.stringify(body));
beforeEach(() => { vi.stubGlobal("crypto", webcrypto); });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });

it("binds the explicit write and verified receipt to the exact accepted copy and actor", async () => {
  const token = randomBytes(32).toString("base64url"); setSessionToken(token);
  const fetch = vi.fn(async () => json(retirementDto())); vi.stubGlobal("fetch", fetch);
  const body = retirementRequest(), result = await api.retire(materialId, copyJob, actorId, body);
  expect(result.status).toBe("REMOVED"); expect(result.id).toBe(retirementId);
  expect(fetch).toHaveBeenCalledOnce();
  expect(fetch.mock.calls[0]).toEqual([`/api/materials/${materialId}/packaging-executions/${copyJob.id}/retirement`, expect.objectContaining({
    method: "POST", credentials: "same-origin", body: JSON.stringify(body), headers: expect.objectContaining({ "X-CSRF-Token": token }),
  })]);
});

it.each(["material_id", "execution_id", "accepted_observation_id", "proof_sha256", "status", "last_dispatch_id", "file_count", "byte_count", "reason", "receipt"])("rejects invalid intent %s", (key) => {
  const dto: Record<string, unknown> = retirementDto();
  dto[key] = key === "last_dispatch_id" ? null : key === "reason" ? " " : key.endsWith("_id") ? actorId : key.endsWith("count") ? -1 : "INVALID";
  expect(() => retirementFromDto(dto, materialId, copyJob)).toThrow();
});
it.each(["schema_version", "status", "operation_id", "request_hash", "plan_hash", "proof_sha256", "retirement_id", "retirement_request_hash", "file_count", "byte_count", "extra"])("rejects substituted receipt %s", (key) => {
  const dto = retirementDto(); (dto.receipt as Record<string, unknown>)[key] = "INVALID";
  expect(() => retirementFromDto(dto, materialId, copyJob)).toThrow();
});
it.each(["RESERVED", "RUNNING", "RECOVERY_REQUIRED"])("never reports %s as verified removal", (status) => {
  const dto = retirementDto(status); expect(retirementFromDto(dto, materialId, copyJob).status).toBe(status);
  dto.receipt = retirementDto().receipt; expect(() => retirementFromDto(dto, materialId, copyJob)).toThrow();
});
it("rejects a mismatched actor or intent after a write and preserves an explicit recovery target", async () => {
  const fetch = vi.fn(async () => json(retirementDto())); vi.stubGlobal("fetch", fetch);
  await expect(api.retire(materialId, copyJob, materialId, retirementRequest())).rejects.toThrow();
  const body = { idempotency_key: crypto.randomUUID(), expected_proof_sha256: copyJob.proofSha256,
    expected_retirement_id: retirementId, expected_last_dispatch_id: dispatchId, acknowledgement: "REMOVE_LOCAL_COPY" as const, reason: "Explicit recovery" };
  await expect(api.recover(materialId, copyJob, body)).resolves.toMatchObject({ id: retirementId });
  await expect(api.recover(materialId, copyJob, { ...body, expected_retirement_id: materialId })).rejects.toThrow();
  expect(fetch).toHaveBeenCalledTimes(3);
});
it("rejects a changed proof before any IO and never retries transport errors", async () => {
  const fetch = vi.fn(async () => { throw new TypeError("Synthetic response loss"); }); vi.stubGlobal("fetch", fetch);
  await expect(api.retire(materialId, copyJob, actorId, { ...retirementRequest(), expected_proof_sha256: "e".repeat(64) })).rejects.toThrow();
  expect(fetch).not.toHaveBeenCalled();
  await expect(api.retire(materialId, copyJob, actorId, retirementRequest())).rejects.toThrow(); expect(fetch).toHaveBeenCalledOnce();
});
it("validates ordered history and bounded cursors", async () => {
  const page = { items: [removalAction()], next_cursor: 1 as number | null };
  vi.stubGlobal("fetch", vi.fn(async () => json(page)));
  await expect(api.history(materialId, copyJob)).resolves.toMatchObject({ nextCursor: 1 });
  await expect(api.history(materialId, copyJob, 1)).rejects.toThrow();
  page.items.push(removalAction()); await expect(api.history(materialId, copyJob)).rejects.toThrow();
  page.items.pop(); page.next_cursor = 2; await expect(api.history(materialId, copyJob)).rejects.toThrow();
  page.next_cursor = null; page.items[0].observation.failure_code = "PACKAGING_PRIVATE";
  await expect(api.history(materialId, copyJob)).rejects.toThrow();
});
