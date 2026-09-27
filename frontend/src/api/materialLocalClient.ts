import { request } from "./client";
import { boolean, nullable, record, string, uuid } from "./dto";

export const materialLocalClient = {
  async info(id: string) {
    const value = record(await request(`/materials/${uuid(id)}/local-files`));
    return { absolutePath: nullable(value.absolute_path), canOpen: boolean(value.can_open), canMove: boolean(value.can_move) };
  },
  async openFolder(id: string) { await request(`/materials/${uuid(id)}/local-files/open-folder`, "POST"); },
  async openMetadata(id: string) { await request(`/materials/${uuid(id)}/local-files/open-metadata`, "POST"); },
  async destination(id: string) {
    const value = record(await request(`/materials/${uuid(id)}/local-files/select-destination`, "POST"));
    return { absolutePath: nullable(value.destination_path), parent: nullable(value.target_parent) };
  },
  async check(id: string) {
    const value = record(await request(`/materials/${uuid(id)}/check-data`, "POST"));
    if (!Array.isArray(value.issues) || value.issues.length > 4096) throw new Error("Invalid check issues");
    return { report: string(value.report), issues: value.issues.map(string) };
  },
};
