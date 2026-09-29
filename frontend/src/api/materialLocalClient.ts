import { request } from "./client";
import { boolean, nullable, record, string, uuid } from "./dto";
import type { Material } from "./materialDto";
import { runFileCheck, type FileCheckOptions } from "./materialCheckJobs";
import { sessionGeneration } from "../auth/sessionTransport";
export type { AutomaticFileCheckResult } from "./materialCheckJobs";

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
  async check(id: string, options: FileCheckOptions = {}) {
    const generation = sessionGeneration();
    const updatedAt = options.expectedUpdatedAt ?? string(record(await request(`/materials/${uuid(id)}`, "GET", undefined, undefined, options.signal)).updated_at);
    if (generation !== sessionGeneration()) throw new DOMException("Session changed", "AbortError");
    return (await runFileCheck([{ id, updatedAt }], false, options)).items[0];
  },
  async checkMany(materials: Pick<Material, "id" | "updatedAt">[], openReport = true, options: FileCheckOptions = {}) {
    return runFileCheck(materials, openReport, options);
  },
};
