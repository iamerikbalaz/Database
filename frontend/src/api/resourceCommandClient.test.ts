import { afterEach, expect, it, vi } from "vitest";
import { companies, brands, projects } from "./mockData";
import { companyToDto, publishedBrandToDto, projectToDto } from "./dto";
import { materialDto, processorDto } from "../test/materialFixtures";
import { resourceCommandClient, resourceReceipt, resourceRequestHash, type CommandKind, type CommandScope } from "./resourceCommandClient";
import { httpApiClient } from "./client";

const actor = "a0000000-0000-4000-8000-000000000001", key = "b0000000-0000-4000-8000-000000000002";
const cases: { kind: CommandKind; response: Record<string, unknown> & { id: string } }[] = [
  { kind: "COMPANY", response: { ...companyToDto(companies[0]) } },
  { kind: "BRAND", response: { ...publishedBrandToDto(brands[0]) } },
  { kind: "PROJECT", response: { ...projectToDto(projects[0]) } },
  { kind: "USER", response: { ...processorDto } }, { kind: "MATERIAL", response: { ...materialDto } },
];
const scope = (kind: CommandKind, targetId: string): CommandScope => ({ kind, action: "UPDATED", targetId, editorPath: "/synthetic/edit" });
function receipt(kind: CommandKind, response: Record<string, unknown> & { id: string }) {
  return { id: key, actor_id: actor, request_key: key, kind, action: "UPDATED", resource_id: response.id, response,
    request_hash: "a".repeat(64), response_hash: "b".repeat(64), created_at: "2026-09-18T08:00:00Z" };
}
afterEach(() => { vi.unstubAllGlobals(); });

it.each(cases)("accepts only a complete actor/key/target-bound $kind receipt", ({ kind, response }) => {
  const value = receipt(kind, response);
  expect(resourceReceipt(value, scope(kind, response.id), actor, key, "a".repeat(64)).resourceId).toBe(response.id);
  for (const changed of [
    { ...value, actor_id: key }, { ...value, request_key: actor }, { ...value, action: "CREATED" },
    { ...value, resource_id: actor }, { ...value, response: { ...response, id: key } },
    { ...value, response: { ...response, unapproved_field: "Synthetic" } },
    { ...value, request_hash: "invalid" }, { ...value, request_hash: "c".repeat(64) }, { ...value, response_hash: null }, { ...value, created_at: "invalid" },
  ]) expect(() => resourceReceipt(changed, scope(kind, response.id), actor, key, "a".repeat(64))).toThrow();
});

it("matches the server's raw JSON digest before trimming, defaults and URL normalization", async () => {
  const scope: CommandScope = { kind: "COMPANY", action: "CREATED", targetId: null, editorPath: "/companies/new" };
  const payload = { name: "  Český kámen 🪨  ", website: "https://example.invalid", is_active: true, country: null };
  expect(await resourceRequestHash(scope, payload)).toBe("e07f34e14ab4bc4ebdd71095a460dc2fa38dc56310d5d2d899038c16915a8962");
  expect(await resourceRequestHash(scope, { country: null, is_active: true, website: payload.website, name: payload.name })).toBe(await resourceRequestHash(scope, payload));
  expect(await resourceRequestHash(scope, { ...payload, name: payload.name.trim() })).not.toBe(await resourceRequestHash(scope, payload));
  const edit: CommandScope = { ...scope, action: "UPDATED", targetId: actor };
  expect(await resourceRequestHash({ ...edit, targetId: actor.toUpperCase() }, payload)).toBe(await resourceRequestHash(edit, payload));
});

it("recovers explicitly with GET and validates the key before issuing a request", async () => {
  const { kind, response } = cases[0];
  const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify(receipt(kind, response)), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  await resourceCommandClient.recover(scope(kind, response.id), actor, key, "a".repeat(64));
  expect(fetch).toHaveBeenCalledWith(`/api/resource-commands/${key}`, expect.objectContaining({ method: "GET", cache: "no-store" }));
  await expect(resourceCommandClient.recover(scope(kind, response.id), actor, "invalid", "a".repeat(64))).rejects.toThrow();
  expect(fetch).toHaveBeenCalledOnce();
});

it("sends the stable request key separately from a material's writable values", async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify(materialDto), { status: 201 }));
  vi.stubGlobal("fetch", fetch);
  await httpApiClient.createMaterial(materialDto, key);
  const init = fetch.mock.calls[0][1]!;
  expect(init.headers).toMatchObject({ "Idempotency-Key": key });
  expect(JSON.parse(init.body as string)).not.toHaveProperty("request_key");
  await expect(httpApiClient.createMaterial(materialDto, "invalid")).rejects.toThrow();
  expect(fetch).toHaveBeenCalledOnce();
});
