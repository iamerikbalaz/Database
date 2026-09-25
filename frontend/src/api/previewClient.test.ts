import { afterEach, expect, it, vi } from "vitest";
import { previewClient, previewListingFromDto } from "./previewClient";
import { setSessionToken, subscribeSessionInvalidation } from "../auth/sessionTransport";
import { materialDto } from "../test/materialFixtures";

const id = materialDto.id, entry = { name: "Sample & view.png", size: 12, sha256: "a".repeat(64) };
const listing = () => ({ material_id: id, missing: false, ignored_entries: 0, items: [entry] });
const bytes = new Uint8Array([255, 216, 255, 224, 0, 255, 217]); // Transport fixture; real decoding is tested on Linux and in E2E.
const headers = { "Content-Type": "image/jpeg", "X-Preview-Width": "512", "X-Preview-Height": "256", "X-Preview-Sha256": "b".repeat(64) };
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
it("binds a validated listing to the requested material", async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify(listing()))); vi.stubGlobal("fetch", fetch);
  expect((await previewClient.listing(id)).items).toEqual([entry]); expect(fetch).toHaveBeenCalledWith(`/api/materials/${id}/previews`, expect.any(Object));
});
it.each([
  { material_id: "00000000-0000-4000-8000-000000000009" }, { missing: true }, { ignored_entries: 513 },
  { items: [entry, entry] }, { items: [{ ...entry, size: 0 }] }, { items: [{ ...entry, sha256: "invalid" }] },
  { items: [{ ...entry, name: "../secret.png" }] }, { items: [{ ...entry, name: "unsafe.svg" }] },
  { items: Array.from({ length: 65 }, (_, i) => ({ ...entry, name: `${i}.png` })) },
])("rejects malformed or inconsistent listings (%j)", (change) => {
  expect(() => previewListingFromDto({ ...listing(), ...change }, id)).toThrow();
});
it("loads a bounded same-origin image through the authenticated API with an abort signal", async () => {
  const fetch = vi.fn(async () => new Response(bytes, { headers })); vi.stubGlobal("fetch", fetch);
  const signal = new AbortController().signal; const result = await previewClient.image(id, entry, signal);
  expect(result).toMatchObject({ width: 512, height: 256 }); expect(result.blob.size).toBe(bytes.length);
  expect(fetch).toHaveBeenCalledWith(`/api/materials/${id}/preview?name=Sample+%26+view.png&expected_sha256=${entry.sha256}`,
    expect.objectContaining({ signal, credentials: "same-origin", cache: "no-store", headers: { Accept: "image/jpeg" } }));
});
it("requests a smaller thumbnail and rejects a response larger than that size", async () => {
  const fetch = vi.fn(async () => new Response(bytes, { headers: { ...headers, "X-Preview-Width": "256" } })); vi.stubGlobal("fetch", fetch);
  const signal = new AbortController().signal;
  expect((await previewClient.image(id, entry, signal, 256)).width).toBe(256);
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining("&size=256"), expect.objectContaining({ signal }));
  fetch.mockImplementation(async () => new Response(bytes, { headers }));
  await expect(previewClient.image(id, entry, signal, 256)).rejects.toThrow();
});
it.each([
  { "Content-Type": "image/svg+xml" }, { "X-Preview-Width": "1025" }, { "X-Preview-Height": "0" }, { "X-Preview-Sha256": "bad" },
  { "Content-Length": "2097153" }, { "Content-Length": "8" }, { "Content-Length": "x" },
])("rejects invalid image headers or length (%j)", async (change) => {
  const changedHeaders = new Headers(headers);
  for (const [key, value] of Object.entries(change)) if (value !== undefined) changedHeaders.set(key, value);
  vi.stubGlobal("fetch", vi.fn(async () => new Response(bytes, { headers: changedHeaders })));
  await expect(previewClient.image(id, entry, new AbortController().signal)).rejects.toThrow();
});
it.each([new Uint8Array([1, 2, 3]), new Uint8Array(2097153)])("rejects incorrect or oversized image bytes", async (body) => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { headers })));
  await expect(previewClient.image(id, entry, new AbortController().signal)).rejects.toThrow();
});
it("stops an oversized stream with no declared length and cancels both cloned branches", async () => {
  const cancel = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    pull(controller) { controller.enqueue(new Uint8Array(1024 ** 2)); },
    cancel,
  });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { headers })));
  await expect(previewClient.image(id, entry, new AbortController().signal)).rejects.toThrow("too large");
  expect(cancel).toHaveBeenCalledOnce();
});
it.each([401, 403])("invalidates expired or forced-change sessions on image response %s", async (status) => {
  const listener = vi.fn(), stop = subscribeSessionInvalidation(listener); setSessionToken("synthetic");
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: { code: "PASSWORD_CHANGE_REQUIRED" } }), { status })));
  try { await expect(previewClient.image(id, entry, new AbortController().signal)).rejects.toThrow(); expect(listener).toHaveBeenCalledWith(status === 401 ? "expired" : "password-required"); }
  finally { stop(); }
});
