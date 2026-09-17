import { afterEach, expect, it, vi } from "vitest";
import { aiServiceClient } from "./aiServiceClient";
import { materialDto, processorDto } from "../test/materialFixtures";

const id = materialDto.id, grantId = "10000000-0000-4000-8000-000000000001";
const credential = { id: grantId, material_id: id, actor_id: processorDto.id, created_at: "2026-09-17T12:00:00Z", expires_at: "2026-09-17T12:15:00Z", revoked_at: null, scopes: ["publishing-context:read", "content-drafts:write"] };
const body = () => ({ credential: { ...credential, scopes: [...credential.scopes] }, secret_available: true, token: `reawote_ai_${grantId}.${"a".repeat(43)}` as string | null });
const payload = { idempotency_key: grantId, lifetime_seconds: 900, reason: "Synthetic client" };
afterEach(() => vi.unstubAllGlobals());
it("accepts first issuance and exact replay without recovering a secret", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(body()))).mockResolvedValueOnce(new Response(JSON.stringify({ ...body(), secret_available: false, token: null })));
  vi.stubGlobal("fetch", fetcher);
  expect((await aiServiceClient.issue(id, payload)).token).toBe(body().token);
  expect((await aiServiceClient.issue(id, payload)).token).toBeNull();
  expect(fetcher).toHaveBeenNthCalledWith(1, expect.stringContaining("/ai-service-credentials"), expect.objectContaining({ method: "POST", credentials: "same-origin", body: JSON.stringify(payload) }));
});
it.each(["material", "scope", "time", "token", "replay"])("rejects inconsistent issuance %s without echoing a secret", async (defect) => {
  const value = body();
  if (defect === "material") value.credential.material_id = grantId;
  if (defect === "scope") value.credential.scopes.push("publish");
  if (defect === "time") value.credential.expires_at = "2026-09-17T11:00:00Z";
  if (defect === "token") value.token = "SYNTHETIC_INVALID_SECRET";
  if (defect === "replay") value.secret_available = false;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(value))));
  await expect(aiServiceClient.issue(id, payload)).rejects.toThrow(/Invalid|Wrong|Unexpected/);
});
it("rejects an unconfirmed or unrelated revocation", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(credential))));
  await expect(aiServiceClient.revoke(id, grantId, { idempotency_key: grantId, reason: "Stop service" })).rejects.toThrow("Invalid credential revocation");
});
it("rejects duplicate paginated metadata and strips unrelated fields", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ items: [credential, credential], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ ...credential, unexpected: "unused" }], next_cursor: null })));
  vi.stubGlobal("fetch", fetcher);
  await expect(aiServiceClient.history(id)).rejects.toThrow("Duplicate");
  expect((await aiServiceClient.history(id)).items[0]).not.toHaveProperty("unexpected");
});
