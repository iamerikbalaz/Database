import { afterEach, expect, it, vi } from "vitest";
import { previewClient } from "./previewClient";
import { materialDto } from "../test/materialFixtures";
import { setSessionToken, subscribeSessionInvalidation } from "../auth/sessionTransport";

const bytes = new Uint8Array([137,80,78,71,13,10,26,10,0,0,0,0,73,69,78,68,174,66,96,130]);
async function fixture() {
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))).map(value => value.toString(16).padStart(2,"0")).join("");
  return { entry: { name: "SPHERE_1.png", size: bytes.length, sha256: hash }, headers: { "Content-Type":"image/png", "X-Preview-Width":"1200", "X-Preview-Height":"1200", "X-Preview-Sha256":hash } };
}
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });

it("requests authenticated original bytes with a pinned hash and retains full dimensions", async () => {
  const { entry, headers } = await fixture(), fetch = vi.fn(async () => new Response(bytes, { headers })); vi.stubGlobal("fetch", fetch);
  const signal = new AbortController().signal;
  const result = await previewClient.original(materialDto.id, entry, signal);
  expect(result).toMatchObject({ width:1200, height:1200 }); expect(result.blob.type).toBe("image/png");
  expect(result.blob.size).toBe(bytes.length);
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining(`/preview-original?name=SPHERE_1.png&expected_sha256=${entry.sha256}`), expect.objectContaining({ signal, credentials:"same-origin", cache:"no-store" }));
});

it.each([{"Content-Type":"image/svg+xml"}, {"Content-Length":"67108865"}, {"X-Preview-Width":"32769"}, {"X-Preview-Width":"8192","X-Preview-Height":"8192"}, {"X-Preview-Sha256":"f".repeat(64)}])("rejects unsafe or changed original headers (%j)", async changed => {
  const { entry, headers } = await fixture(), modified = new Headers(headers);
  for (const [key,value] of Object.entries(changed)) if (value !== undefined) modified.set(key,value);
  vi.stubGlobal("fetch", vi.fn(async () => new Response(bytes, { headers:modified })));
  await expect(previewClient.original(materialDto.id, entry, new AbortController().signal)).rejects.toThrow();
});

it("rejects changed bytes even if response hash and length claim to match", async () => {
  const { entry, headers } = await fixture(), changed = bytes.slice(); changed[9] = 1;
  vi.stubGlobal("fetch", vi.fn(async () => new Response(changed, { headers })));
  await expect(previewClient.original(materialDto.id, entry, new AbortController().signal)).rejects.toThrow("changed");
});

it("invalidates an expired session on original image reads", async () => {
  const { entry } = await fixture(), listener = vi.fn(); setSessionToken("synthetic"); const stop = subscribeSessionInvalidation(listener);
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", {status:401})));
  try { await expect(previewClient.original(materialDto.id, entry, new AbortController().signal)).rejects.toThrow(); expect(listener).toHaveBeenCalledWith("expired"); }
  finally { stop(); }
});
