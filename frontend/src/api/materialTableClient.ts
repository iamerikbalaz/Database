import { request } from "./client";
import { uuid } from "./dto";
import { materialFromDto, parseMaterial, type Material } from "./materialDto";

export type TableChange = { project_id: string | null } | { assigned_processor_id: string }
  | { published_brand_id: string } | { main_category_code: string }
  | { workflow_status: Material["workflowStatus"] } | { checked_status: Material["checkedStatus"] }
  | { is_published: boolean } | { note: string | null };
export const materialTableClient = {
  async update(material: Pick<Material, "id" | "updatedAt">, change: TableChange, key: string) {
    return materialFromDto(parseMaterial(await request(`/materials/${uuid(material.id)}/table`, "PATCH",
      { expected_updated_at: material.updatedAt, ...change }, uuid(key))));
  },
};
