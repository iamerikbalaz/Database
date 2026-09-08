import { parseList, uuid } from "./dto";
import { ApiError } from "./errors";
import { internalUserFromDto, materialFromDto, parseInternalUser, parseMaterial,
  type Material, type InternalUser, type MaterialCreateDto, type MaterialPatchDto } from "./materialDto";

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
  };
}
export function materialLoadError(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) return "Material or selected record not found (404).";
  if (error instanceof ApiError) return error.message;
  return "Materials could not be loaded. Check your connection and try again.";
}
