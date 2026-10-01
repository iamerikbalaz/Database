import { request } from "./client";
import { boolean, nullable, record, string } from "./dto";

function parse(value: unknown) {
  const data = record(value);
  if (!Number.isSafeInteger(data.version) || Number(data.version) < 0) throw new Error("Invalid path settings");
  return { version: Number(data.version), sbsTemplatesRoot: string(data.sbs_templates_root), ordersRoot: string(data.orders_root), materialsRoot: string(data.materials_root), publishedLibraryRoot: string(data.published_library_root), canSelectFolder: boolean(data.can_select_folder) };
}
export type PathSettings = ReturnType<typeof parse>;
export type PathField = "sbs_templates_root" | "orders_root" | "materials_root" | "published_library_root";
export type PathSettingsUpdate = { idempotency_key: string; expected_version: number; sbs_templates_root: string; orders_root: string; materials_root: string; published_library_root: string };
export const pathSettingsClient = {
  async current() { return parse(await request("/settings/paths")); },
  async save(payload: PathSettingsUpdate) { const saved = parse(await request("/settings/paths", "POST", payload)); if (saved.version !== payload.expected_version + 1) throw new Error("Path settings version mismatch"); return saved; },
  async selectFolder(field: PathField) { return nullable(record(await request("/settings/paths/select-folder", "POST", { field })).folder_path); },
};
