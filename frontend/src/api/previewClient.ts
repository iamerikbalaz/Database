import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";
import { apiUrl, notifySessionInvalidation, sessionGeneration } from "../auth/sessionTransport";

const MAX_IMAGE_BYTES = 2 * 1024 ** 2;
function integer(input: unknown, max: number, min = 0): number {
  if (typeof input !== "number" || !Number.isSafeInteger(input) || input < min || input > max) throw new Error("Invalid preview response");
  return input;
}
function hash(input: unknown) {
  const value = string(input);
  if (!/^[a-f0-9]{64}$/.test(value)) throw new Error("Invalid preview hash");
  return value;
}
function name(input: unknown) {
  const value = string(input);
  if (!value || new TextEncoder().encode(value).length > 255 || /[\p{C}\\/:]/u.test(value) || !/\.(jpe?g|png|tiff?|webp)$/i.test(value)) throw new Error("Invalid preview name");
  return value;
}
export function previewListingFromDto(input: unknown, materialId: string) {
  const value = record(input);
  if (uuid(value.material_id) !== uuid(materialId) || !Array.isArray(value.items) || value.items.length > 64) throw new Error("Invalid preview listing");
  const items = value.items.map((entry) => {
    const item = record(entry);
    return { name: name(item.name), sha256: hash(item.sha256), size: integer(item.size, 64 * 1024 ** 2, 1) };
  });
  const missing = boolean(value.missing), ignoredEntries = integer(value.ignored_entries, 512);
  if (new Set(items.map((item) => item.name)).size !== items.length || (missing && (items.length || ignoredEntries)) ||
      items.length + ignoredEntries > 512 || items.reduce((total, item) => total + item.size, 0) > 512 * 1024 ** 2) throw new Error("Inconsistent preview listing");
  return { items, missing, ignoredEntries };
}
export type PreviewEntry = ReturnType<typeof previewListingFromDto>["items"][number];
export const previewClient = {
  async listing(materialId: string) {
    return previewListingFromDto(await request(`/materials/${uuid(materialId)}/previews`), materialId);
  },
  async image(materialId: string, entry: PreviewEntry, signal: AbortSignal) {
    const query = new URLSearchParams({ name: name(entry.name), expected_sha256: hash(entry.sha256) });
    const sentGeneration = sessionGeneration();
    const response = await fetch(apiUrl(`/materials/${uuid(materialId)}/preview?${query}`), {
      credentials: "same-origin", cache: "no-store", headers: { Accept: "image/jpeg" }, signal,
    });
    if (!response.ok) {
      const body: unknown = await response.json().catch(() => null);
      notifySessionInvalidation(response.status, body, sentGeneration);
      throw new Error("Preview could not be loaded");
    }
    const declaredLength = response.headers.get("Content-Length");
    let width: number, height: number;
    try {
      width = integer(Number(response.headers.get("X-Preview-Width")), 1024, 1);
      height = integer(Number(response.headers.get("X-Preview-Height")), 1024, 1);
      hash(response.headers.get("X-Preview-Sha256"));
      if (response.headers.get("Content-Type") !== "image/jpeg" || !response.body ||
          (declaredLength !== null && (!/^\d+$/.test(declaredLength) || Number(declaredLength) > MAX_IMAGE_BYTES))) throw new Error("Invalid preview image");
    } catch {
      await response.body?.cancel(); throw new Error("Invalid preview image");
    }
    if (!response.body) throw new Error("Missing preview image");
    // Bound a cloned stream before consuming the original with the browser's
    // native body reader. Direct stream-to-Blob reads can report a completed
    // image as ERR_ABORTED in Chromium. Cloning does not make another request;
    // the original branch buffers only the bytes allowed by this limit.
    const reader = response.clone().body!.getReader();
    let length = 0; let finished = false;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) { finished = true; break; }
        length += value.length;
        if (length > MAX_IMAGE_BYTES) throw new Error("Preview image is too large");
      }
    } finally {
      try { if (!finished) await Promise.allSettled([reader.cancel(), response.body.cancel()]); }
      finally { reader.releaseLock(); }
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.length !== length || length < 5 || bytes[0] !== 255 || bytes[1] !== 216 || bytes[2] !== 255 || bytes[length - 2] !== 255 || bytes[length - 1] !== 217 ||
        (declaredLength !== null && Number(declaredLength) !== length)) throw new Error("Invalid preview image");
    return { blob: new Blob([bytes], { type: "image/jpeg" }), width, height };
  },
};
