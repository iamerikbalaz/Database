import { sessionGeneration } from "../auth/sessionTransport";
import { previewClient, type PreviewEntry } from "./previewClient";

type Listing = Awaited<ReturnType<typeof previewClient.listing>>;
type Image = Awaited<ReturnType<typeof previewClient.image>>;
type Cached<T> = { value: T; until: number };
const MAX_BYTES = 32 * 1024 ** 2;
const abort = () => new DOMException("Preview request cancelled", "AbortError");

export function orderedGalleryEntries(items: PreviewEntry[]) {
  const priority = (name: string) => name.toUpperCase() === "FABRIC_1.PNG" ? 0 : name.toUpperCase() === "SPHERE_1.PNG" ? 1 : 2;
  return items.filter(item => /\.png$/i.test(item.name)).sort((a, b) => priority(a.name) - priority(b.name) || a.name.localeCompare(b.name, "en", { numeric: true }));
}

// Owned by one authenticated Materials page. Never persists pixels or filenames.
export class GalleryStore {
  private active = 0;
  private waiting: (() => void)[] = [];
  private listings = new Map<string, Cached<Listing>>();
  private images = new Map<string, Cached<Image>>();
  private bytes = 0;
  private epoch = 0;
  constructor(private generation = sessionGeneration()) {}
  clear() { this.epoch++; this.listings.clear(); this.images.clear(); this.bytes = 0; }
  forget(id: string, folder: string) {
    const key = JSON.stringify([id, folder]); this.listings.delete(key);
    for (const [imageKey, item] of this.images) if (imageKey.startsWith(key.slice(0, -1) + ",")) {
      this.bytes -= item.value.blob.size; this.images.delete(imageKey);
    }
  }
  private check(signal: AbortSignal, epoch: number) {
    if (signal.aborted || epoch !== this.epoch || this.generation !== sessionGeneration()) throw abort();
  }
  private run<T>(signal: AbortSignal, task: () => Promise<T>): Promise<T> {
    const epoch = this.epoch;
    return new Promise((resolve, reject) => {
      const start = () => {
        signal.removeEventListener("abort", cancel);
        try { this.check(signal, epoch); } catch (error) { reject(error); return; }
        this.active++;
        void task().then(value => { this.check(signal, epoch); resolve(value); }).catch(reject).finally(() => { this.active--; this.pump(); });
      };
      const cancel = () => {
        const index = this.waiting.indexOf(start);
        if (index !== -1) { this.waiting.splice(index, 1); signal.removeEventListener("abort", cancel); reject(abort()); }
      };
      signal.addEventListener("abort", cancel, { once: true });
      this.waiting.push(start);
      this.pump();
    });
  }
  private pump() { while (this.active < 2 && this.waiting.length) this.waiting.shift()!(); }
  listing(id: string, folder: string, signal: AbortSignal): Promise<Listing> {
    const key = JSON.stringify([id, folder]); const cached = this.listings.get(key);
    if (cached && cached.until > Date.now() && this.generation === sessionGeneration() && !signal.aborted) return Promise.resolve(cached.value);
    const epoch = this.epoch;
    return this.run(signal, async () => {
      const value = await previewClient.listing(id, signal); this.check(signal, epoch);
      this.listings.delete(key); this.listings.set(key, { value, until: Date.now() + 60_000 });
      if (this.listings.size > 200) this.listings.delete(this.listings.keys().next().value!);
      return value;
    });
  }
  image(id: string, folder: string, entry: PreviewEntry, size: 256 | 512, signal: AbortSignal): Promise<Image> {
    const key = JSON.stringify([id, folder, entry.name, entry.sha256, size]); const cached = this.images.get(key);
    if (cached && cached.until > Date.now() && this.generation === sessionGeneration() && !signal.aborted) {
      this.images.delete(key); this.images.set(key, cached); return Promise.resolve(cached.value);
    }
    const epoch = this.epoch;
    return this.run(signal, async () => {
      const value = await previewClient.image(id, entry, signal, size); this.check(signal, epoch);
      const old = this.images.get(key); if (old) this.bytes -= old.value.blob.size;
      this.images.delete(key); this.images.set(key, { value, until: Date.now() + 60_000 }); this.bytes += value.blob.size;
      while (this.bytes > MAX_BYTES || this.images.size > 160) {
        const oldest = this.images.keys().next().value!; this.bytes -= this.images.get(oldest)!.value.blob.size; this.images.delete(oldest);
      }
      return value;
    });
  }
}
