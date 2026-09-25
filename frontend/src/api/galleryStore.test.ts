import { afterEach, expect, it, vi } from "vitest";
import { GalleryStore, orderedGalleryEntries } from "./galleryStore";
import { previewClient } from "./previewClient";
import { setSessionToken } from "../auth/sessionTransport";

const entry = { name: "SPHERE_1.png", sha256: "a".repeat(64), size: 20 };
const listing = { missing: false, ignoredEntries: 0, items: [entry] };
const pixels = () => ({ blob: new Blob(["pixels"]), width: 256, height: 256 });
afterEach(() => { vi.restoreAllMocks(); setSessionToken(null); });
it("prioritizes fabric then sphere and includes only PNG with natural filename order", () => {
  const entries = ["VIEW_10.png", "OTHER.jpg", "sphere_1.PNG", "FABRIC_1.png", "VIEW_2.png"].map(name => ({ ...entry, name }));
  expect(orderedGalleryEntries(entries).map(item => item.name)).toEqual(["FABRIC_1.png", "sphere_1.PNG", "VIEW_2.png", "VIEW_10.png"]);
});
it("limits all preview work to two requests and cancels a queued card without network", async () => {
  const finish: (() => void)[] = [];
  const read = vi.spyOn(previewClient, "listing").mockImplementation(() => new Promise(resolve => finish.push(() => resolve(listing))));
  const store = new GalleryStore(); const signals = Array.from({ length: 4 }, () => new AbortController());
  const reads = signals.map((signal, i) => store.listing(String(i), "folder", signal.signal));
  expect(read).toHaveBeenCalledTimes(2);
  const cancelled = expect(reads[2]).rejects.toMatchObject({ name: "AbortError" }); signals[2].abort(); await cancelled;
  finish[0](); await reads[0]; await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(3));
  finish[1](); finish[2](); await Promise.all([reads[1], reads[3]]);
  expect(read.mock.calls.map(call => call[0])).toEqual(["0", "1", "3"]);
});
it("reuses bounded memory pixels, keys by folder/hash/size, and refreshes on request", async () => {
  const read = vi.spyOn(previewClient, "image").mockImplementation(async () => pixels());
  const store = new GalleryStore(); const signal = new AbortController().signal;
  await store.image("id", "folder", entry, 256, signal); await store.image("id", "folder", entry, 256, signal);
  expect(read).toHaveBeenCalledTimes(1);
  await store.image("id", "folder", entry, 512, signal);
  await store.image("id", "renamed", entry, 256, signal);
  await store.image("id", "folder", { ...entry, sha256: "b".repeat(64) }, 256, signal);
  expect(read).toHaveBeenCalledTimes(4);
  store.forget("id", "folder"); await store.image("id", "folder", entry, 256, signal);
  expect(read).toHaveBeenCalledTimes(5);
});
it("never returns cached pixels or late requests after authentication changes", async () => {
  vi.spyOn(previewClient, "image").mockImplementation(async () => pixels());
  const store = new GalleryStore(); const signal = new AbortController().signal;
  await store.image("id", "folder", entry, 256, signal); setSessionToken("another session");
  await expect(store.image("id", "folder", entry, 256, signal)).rejects.toMatchObject({ name: "AbortError" });
});
it("evicts old thumbnails when the 32 MiB memory budget is reached", async () => {
  const read = vi.spyOn(previewClient, "image").mockImplementation(async () => ({ ...pixels(), blob: new Blob([new Uint8Array(2 * 1024 ** 2)]) }));
  const store = new GalleryStore(); const signal = new AbortController().signal;
  for (let i = 0; i < 17; i++) await store.image(String(i), "folder", entry, 512, signal);
  await store.image("16", "folder", entry, 512, signal); expect(read).toHaveBeenCalledTimes(17);
  await store.image("0", "folder", entry, 512, signal); expect(read).toHaveBeenCalledTimes(18);
});
it("expires cache entries and discards a late read after refresh", async () => {
  let now = 1000; vi.spyOn(Date, "now").mockImplementation(() => now);
  const read = vi.spyOn(previewClient, "listing").mockResolvedValue(listing);
  const store = new GalleryStore(); const signal = new AbortController().signal;
  await store.listing("id", "folder", signal); now += 61_000; await store.listing("id", "folder", signal);
  expect(read).toHaveBeenCalledTimes(2);
  let finish!: (value: typeof listing) => void;
  read.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const pending = store.listing("other", "folder", signal); store.clear(); finish(listing);
  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
});
