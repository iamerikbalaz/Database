import { parseList, uuid } from "./dto";
import { ApiError } from "./errors";
import { internalUserFromDto, materialFromDto, parseInternalUser, parseMaterial,
  type Material, type InternalUser, type MaterialCreateDto, type MaterialPatchDto } from "./materialDto";
import {
  materialFolderLinkFromDto,
  materialFolderPreflightFromDto,
  materialMarkDoneFromDto,
  materialMetadataFromDto,
  materialMetadataSnapshotFromDto,
  parseMaterialFolderLink,
  parseMaterialFolderPreflight,
  parseMaterialMarkDone,
  parseMaterialMetadata,
  parseMaterialMetadataSnapshot,
  type MaterialFolderLinkResult,
  type MaterialFolderPreflight,
  type MaterialFolderRequestDto,
  type MaterialMarkDoneResult,
  type MaterialMetadata,
  type MaterialMetadataSnapshot,
} from "./materialOperationsDto";

export interface MaterialFilters {
  search?: string;
  project_id?: string;
  published_brand_id?: string;
  assigned_processor_id?: string;
  main_category_code?: string;
  workflow_status?: string;
  validation_status?: string;
  publication_status?: string;
  is_published?: string;
}
export interface MaterialApi {
  getMaterials(filters?: MaterialFilters): Promise<Material[]>;
  getMaterial(id: string): Promise<Material>;
  createMaterial(payload: MaterialCreateDto): Promise<Material>;
  updateMaterial(id: string, payload: MaterialPatchDto): Promise<Material>;
  getInternalUsers(activeOnly?: boolean): Promise<InternalUser[]>;
  getInternalUser(id: string): Promise<InternalUser>;
  preflightMaterialFolder(id: string, folderPath: string): Promise<MaterialFolderPreflight>;
  linkMaterialFolder(id: string, folderPath: string): Promise<MaterialFolderLinkResult>;
  markMaterialDone(id: string): Promise<MaterialMarkDoneResult>;
  getMaterialMetadata(id: string): Promise<MaterialMetadata>;
  getMaterialMetadataSnapshots(id: string): Promise<MaterialMetadataSnapshot[]>;
}
type Request = (path: string, method?: string, payload?: object) => Promise<unknown>;
export function materialApi(request: Request): MaterialApi {
  return {
    async getMaterials(filters = {}) {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(filters)) {
        if (value?.trim()) query.set(key, value.trim());
      }
      return parseList(await request("/materials" + (query.size ? "?" + query : "")), parseMaterial).map(materialFromDto);
    },
    async getMaterial(id) {
      return materialFromDto(parseMaterial(await request("/materials/" + uuid(id))));
    },
    async createMaterial(p) {
      // Explicit allowlist also strips extra properties supplied at runtime.
      const payload: MaterialCreateDto = {
        project_id: p.project_id, published_brand_id: p.published_brand_id,
        material_name: p.material_name, main_category_code: p.main_category_code,
        assigned_processor_id: p.assigned_processor_id,
      };
      return materialFromDto(parseMaterial(await request("/materials", "POST", payload)));
    },
    async updateMaterial(id, p) {
      const payload: MaterialPatchDto = {};
      if (p.project_id !== undefined) payload.project_id = p.project_id;
      if (p.material_name !== undefined) payload.material_name = p.material_name;
      if (p.main_category_code !== undefined) payload.main_category_code = p.main_category_code;
      if (p.assigned_processor_id !== undefined) payload.assigned_processor_id = p.assigned_processor_id;
      try {
        return materialFromDto(parseMaterial(await request("/materials/" + uuid(id), "PATCH", payload)));
      } catch (error) {
        if (error instanceof ApiError && error.status === 409 && error.message.includes("main_category_code cannot be changed while folder_path is set")) {
          throw new ApiError(409, "This material is linked to a folder. Changing its category requires a future rename operation.",
            [{ field: "main_category_code", message: "A linked folder requires a rename operation before changing category." }]);
        }
        throw error;
      }
    },
    async getInternalUsers(activeOnly = false) {
      return parseList(await request("/internal-users" + (activeOnly ? "?is_active=true" : "")), parseInternalUser).map(internalUserFromDto);
    },
    async getInternalUser(id) {
      return internalUserFromDto(parseInternalUser(await request("/internal-users/" + uuid(id))));
    },
    async preflightMaterialFolder(id, folderPath) {
      const payload: MaterialFolderRequestDto = { folder_path: folderPath };
      return materialFolderPreflightFromDto(parseMaterialFolderPreflight(
        await request("/materials/" + uuid(id) + "/folder-preflight", "POST", payload),
      ));
    },
    async linkMaterialFolder(id, folderPath) {
      const payload: MaterialFolderRequestDto = { folder_path: folderPath };
      return materialFolderLinkFromDto(parseMaterialFolderLink(
        await request("/materials/" + uuid(id) + "/folder-link", "POST", payload),
      ));
    },
    async markMaterialDone(id) {
      return materialMarkDoneFromDto(parseMaterialMarkDone(
        await request("/materials/" + uuid(id) + "/mark-done", "POST"),
      ));
    },
    async getMaterialMetadata(id) {
      return materialMetadataFromDto(parseMaterialMetadata(
        await request("/materials/" + uuid(id) + "/metadata"),
      ));
    },
    async getMaterialMetadataSnapshots(id) {
      return parseList(
        await request("/materials/" + uuid(id) + "/metadata/snapshots"),
        parseMaterialMetadataSnapshot,
      ).map(materialMetadataSnapshotFromDto);
    },
  };
}
export function materialLoadError(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) return "Material or selected record not found (404).";
  if (error instanceof ApiError) return error.message;
  return "Materials could not be loaded. Check your connection and try again.";
}

export type MaterialOperation = "preflight" | "link" | "done" | "metadata" | "snapshots";

export function materialOperationError(error: unknown, operation: MaterialOperation): string {
  if (!(error instanceof ApiError)) {
    if (!(error instanceof TypeError))
      return "The server returned an unexpected response. No data was changed in the page.";
    return operation === "metadata" || operation === "snapshots"
      ? "The metadata service could not be reached. Check your connection and try again."
      : "The request did not reach the server. Check your connection and try again.";
  }
  if (error.status === 404) return "This material no longer exists (404). Refresh the materials list.";
  if (error.status === 503)
    return operation === "metadata" || operation === "snapshots"
      ? "The metadata service is temporarily unavailable (503). Try again later."
      : "The folder checking service is temporarily unavailable (503). Try again later.";
  if (error.status === 422) {
    if (operation === "preflight") return "The folder path is invalid or the folder could not be checked safely (422). Review the path and findings.";
    if (operation === "link") return "The folder did not pass the required safety checks and was not linked (422).";
    if (operation === "done") return "The linked folder no longer passes the required safety checks, so Done was not applied (422).";
    return "The server returned metadata in an unexpected form (422).";
  }
  if (error.status === 409) {
    if (operation === "link") return "The folder could not be linked because it is already used or the material changed (409). Run the check again.";
    if (operation === "done") return "Done could not be applied because this material is already Done or changed during the check (409).";
  }
  return `The request could not be completed (${error.status}). Please try again.`;
}
