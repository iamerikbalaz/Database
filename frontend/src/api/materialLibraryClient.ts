import { request, type ApiClient } from "./client";
import { catalogClient, contentFromDto } from "./catalogClient";
import { ApiError } from "./errors";
import type { Material } from "./materialDto";
import { metadataClient, type MetadataSave } from "./metadataClient";
import { uuid } from "./dto";
import { materialTableClient, type TableChange } from "./materialTableClient";

export type LibraryField = "color" | "tags" | "credits" | "sample_size";
export type LibraryEditorField = LibraryField | "workflow_status" | "checked_status";
export type LibraryChange = { field: "color"; color: string | null } | { field: "tags"; tags: string[] } |
  { field: "credits"; credits: number | null } | { field: "sample_size"; width: string; height: string } |
  { field: "workflow_status"; status: "DONE" | "IN_PROGRESS" } | { field: "checked_status"; status: "no" | "OK" | "Correction" };
type ContentChange = { idempotency_key: string; expected_updated_at: string; expected_revision: number } &
  ({ field: "tags"; tags: string[] } | { field: "credits"; credits: number | null });
export type LibraryPacket = { kind: "metadata"; body: MetadataSave } | { kind: "content"; body: ContentChange } |
  { kind: "table"; material: Material; change: TableChange; key: string };

export const libraryFieldLabels: Record<LibraryField, string> = { color: "Color", tags: "Append tags", credits: "Credits", sample_size: "Sample size (W × H)" };
function uniqueTags(tags: string[]) {
  const unique = new Map<string, string>();
  for (const tag of tags) {
    const key = tag.toLowerCase();
    if (!unique.has(key)) unique.set(key, tag);
  }
  return [...unique.values()];
}
export function libraryChange(field: LibraryEditorField, value: string, height: string): LibraryChange {
  if (field === "workflow_status") {
    if (value !== "DONE" && value !== "IN_PROGRESS") throw new Error("Select Done or In progress.");
    return { field, status: value };
  }
  if (field === "checked_status") {
    if (value !== "no" && value !== "OK" && value !== "Correction") throw new Error("Select no, OK or Correction.");
    return { field, status: value };
  }
  if (field === "color") {
    if (value && !/^#[A-Fa-f0-9]{6}$/.test(value)) throw new Error("Select a color.");
    return { field, color: value.toUpperCase() || null };
  }
  if (field === "credits") {
    if (value && (!/^\d+$/.test(value) || Number(value) > 2147483647)) throw new Error("Credits must be a nonnegative whole number.");
    return { field, credits: value ? Number(value) : null };
  }
  if (field === "tags") {
    const tags = uniqueTags(value.split(/[\n,]/).map(item => item.trim().normalize("NFC").replace(/ +/g, " ")).filter(Boolean));
    if (!tags.length || tags.length > 100 || tags.some(item => item.length > 100 || /[:\p{C}]/u.test(item))) throw new Error("Enter 1–100 tags, separated by commas or lines, without colons or control characters.");
    return { field, tags };
  }
  const valid = (text: string) => /^\d+(?:\.\d)?$/.test(text) && Number(text) > 0 && Number(text) < 100000000;
  if (!valid(value) || !valid(height)) throw new Error("Enter positive width and height in cm, with at most one decimal place.");
  return { field, width: String(Number(value)), height: String(Number(height)) };
}

export async function prepareLibraryChange(material: Material, change: LibraryChange, client: ApiClient): Promise<{ packet: LibraryPacket; before: string; after: string }> {
  const current = await client.getMaterial(material.id);
  if (current.updatedAt !== material.updatedAt || current.isArchived) throw new ApiError(409, "Material changed or is archived. Refresh before editing.");
  if (change.field === "workflow_status" || change.field === "checked_status") {
    const update: TableChange = change.field === "workflow_status" ? { workflow_status: change.status } : { checked_status: change.status };
    return { packet: { kind: "table", material: { ...material }, change: update, key: crypto.randomUUID() },
      before: change.field === "workflow_status" ? material.workflowStatus === "DONE" ? "Done" : "In progress" : material.checkedStatus,
      after: change.field === "workflow_status" ? change.status === "DONE" ? "Done" : "In progress" : change.status };
  }
  if (change.field === "color" || change.field === "sample_size") {
    const source = await metadataClient.inspect(material.id);
    if (source.expectedUpdatedAt !== material.updatedAt) throw new ApiError(409, "Material changed. Refresh before editing.");
    if (source.active) throw new ApiError(409, "A metadata operation is pending. Recover it on the material card first.");
    if (!source.available || !source.writesEnabled || !source.editable) throw new ApiError(409, "Link an available, editable material folder with source writes enabled before changing color or sample size.");
    const values = { ...source.values, ...(change.field === "color" ? { hex_color: change.color } : { width_cm: change.width, height_cm: change.height }) };
    return { packet: { kind: "metadata", body: { idempotency_key: crypto.randomUUID(), expected_updated_at: source.expectedUpdatedAt, expected_sha256: source.sha256, values } },
      before: change.field === "color" ? source.values.hex_color ?? "No color" : `${source.values.width_cm ?? "—"} × ${source.values.height_cm ?? "—"} cm`,
      after: change.field === "color" ? change.color ?? "No color" : `${change.width} × ${change.height} cm` };
  }
  const content = await catalogClient.content(material.id);
  const tags = change.field === "tags" ? uniqueTags([...content.tags, ...change.tags]) : content.tags;
  if (tags.length > 100) throw new ApiError(422, "Adding these tags would exceed the 100-tag limit.");
  return { packet: { kind: "content", body: { idempotency_key: crypto.randomUUID(), expected_updated_at: material.updatedAt, expected_revision: content.revision,
    ...(change.field === "tags" ? { field: "tags", tags: change.tags } : { field: "credits", credits: change.credits }) } },
    before: change.field === "tags" ? content.tags.join(", ") || "No tags" : String(content.credits ?? "Not set"),
    after: change.field === "tags" ? tags.join(", ") : String(change.credits ?? "Not set") };
}

export async function applyLibraryChange(id: string, packet: LibraryPacket, operationId?: string): Promise<{ status: "COMPLETED" | "RUNNING" | "REJECTED"; operationId?: string }> {
  if (packet.kind === "table") {
    const material = await materialTableClient.update(packet.material, packet.change, packet.key);
    if (material.id !== id) throw new Error("Wrong material in table save receipt.");
    return { status: "COMPLETED" };
  }
  if (packet.kind === "metadata") {
    const result = operationId ? await metadataClient.resume(id, operationId) : await metadataClient.save(id, packet.body);
    return { status: result.status as "COMPLETED" | "RUNNING" | "REJECTED", operationId: result.id };
  }
  const content = contentFromDto(await request(`/materials/${uuid(id)}/library-field`, "POST", packet.body));
  if (content.materialId !== id) throw new Error("Wrong material in library save receipt.");
  return { status: "COMPLETED" };
}
