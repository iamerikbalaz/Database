import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";
import { ApiError } from "./errors";

export function discoveryPath(value: unknown): string {
  const path = string(value);
  const parts = path ? path.split("/") : [];
  if (new TextEncoder().encode(path).length > 2048 || parts.length > 16 || parts.some((part) =>
    !part || part === "." || part === ".." || /[\p{C}\\:]/u.test(part) || new TextEncoder().encode(part).length > 255)) throw new Error("Invalid source folder path");
  return path;
}
export function discoveryFromDto(value: unknown, materialId: string, identity: string, parentPath: string) {
  const data = record(value);
  if (uuid(data.material_id) !== uuid(materialId) || string(data.technical_identity) !== identity || discoveryPath(data.parent_path) !== discoveryPath(parentPath)
      || !Array.isArray(data.directories) || data.directories.length > 512) throw new Error("Invalid source folder listing");
  const parent = parentPath ? parentPath + "/" : "";
  const directories = data.directories.map((entry) => {
    const item = record(entry); const name = discoveryPath(item.name), path = discoveryPath(item.path);
    const identityMatches = boolean(item.identity_matches);
    if (!name || name.includes("/") || path !== parent + name || identityMatches !== (name === identity)) throw new Error("Invalid source folder entry");
    return { name, path, identityMatches };
  });
  if (new Set(directories.map((item) => item.path)).size !== directories.length || typeof data.omitted_entries !== "number"
      || !Number.isSafeInteger(data.omitted_entries) || data.omitted_entries < 0 || data.omitted_entries + directories.length > 4096) throw new Error("Invalid source folder count");
  return { parentPath, directories, omittedEntries: data.omitted_entries };
}
export type FolderDiscovery = ReturnType<typeof discoveryFromDto>;
export const discoveryClient = {
  async listing(materialId: string, identity: string, parentPath: string) {
    return discoveryFromDto(await request(`/materials/${uuid(materialId)}/folder-discovery`, "POST", { parent_path: discoveryPath(parentPath) }), materialId, identity, parentPath);
  },
};
export function discoveryError(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 401) return "Your session ended. Sign in again.";
    if (cause.status === 403) return "Administrator access is required to browse source folders.";
    if (cause.status === 404) return "This material or source folder is no longer available.";
    if (cause.status === 409) return "The material or source folder changed. Reload the material and browse again.";
    if (cause.code === "DISCOVERY_ENTRY_LIMIT" || cause.code === "DISCOVERY_DIRECTORY_LIMIT") return "This folder has too many entries. Enter a more specific parent path.";
    if (cause.code === "INVALID_FOLDER_PATH") return "Use a relative path with forward slashes, or leave it empty for the source root.";
    if (cause.code === "UNSAFE_MATERIAL_PATH") return "This folder cannot be safely browsed. Choose a plain directory under the source root.";
  }
  return "Source folders could not be loaded. Check the path and try again.";
}
