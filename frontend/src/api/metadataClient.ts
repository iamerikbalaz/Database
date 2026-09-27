import { request } from "./client";
import { boolean, nullable, record, string, uuid } from "./dto";

function hash(value: unknown) {
  if (value === null) return null;
  const result = string(value);
  if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid source metadata hash");
  return result;
}
export interface MetadataValues { hex_color: string | null; width_cm: string | null; height_cm: string | null; }
function values(input: unknown): MetadataValues {
  const data = record(input);
  const result = { hex_color: nullable(data.hex_color), width_cm: nullable(data.width_cm), height_cm: nullable(data.height_cm) };
  if (result.hex_color !== null && !/^#[A-F0-9]{6}$/.test(result.hex_color)) throw new Error("Invalid metadata color");
  for (const value of [result.width_cm, result.height_cm]) {
    if (value !== null && (!/^[0-9]+(?:\.[0-9]+)?$/.test(value) || !(Number(value) > 0 && Number(value) < 100000000))) throw new Error("Invalid metadata dimensions");
  }
  return result;
}
function operation(input: unknown) {
  const data = record(input), status = string(data.status);
  if (!["RUNNING", "COMPLETED", "REJECTED"].includes(status)) throw new Error("Invalid metadata outcome");
  const result = data.result === null ? null : record(data.result);
  return { id: uuid(data.id), status, failure: result ? nullable(result.failure_code) : null };
}
export type MetadataOperation = ReturnType<typeof operation>;
export interface MetadataSave { idempotency_key: string; expected_updated_at: string; expected_sha256: string | null; values: MetadataValues; }
function observation(input: unknown) {
  const data = record(input);
  return { available: boolean(data.available), writesEnabled: boolean(data.writes_enabled), editable: boolean(data.editable),
    expectedUpdatedAt: string(data.expected_updated_at), sha256: hash(data.sha256), sourceStatus: string(data.source_status),
    values: values(data.values), active: data.active_operation === null ? null : operation(data.active_operation) };
}
export type MetadataObservation = ReturnType<typeof observation>;
export const metadataClient = {
  async inspect(id: string) { return observation(await request(`/materials/${uuid(id)}/source-metadata`)); },
  async save(id: string, payload: MetadataSave) { return operation(await request(`/materials/${uuid(id)}/source-metadata`, "POST", payload)); },
  async resume(id: string, operationId: string) { return operation(await request(`/materials/${uuid(id)}/source-metadata/${uuid(operationId)}/resume`, "POST")); },
};
