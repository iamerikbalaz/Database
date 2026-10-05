import { afterEach, expect, it, vi } from "vitest";
import * as transport from "./client";
import { localPublicationClient, localPublicationJob, localPublicationPreview } from "./localPublicationClient";
import { publicationId, publicationPreviewDto } from "../test/publicationFixtures";
import { materialDto } from "../test/materialFixtures";

afterEach(() => vi.restoreAllMocks());
const selected = [materialDto.id];
const completed = () => ({ id: publicationId, status: "COMPLETED", material_ids: selected,
  output_path: "C:\\Exports\\REAWOTE-test", csv_name: "materials.csv", error_code: null, published: false,
  archives: [{ name: "material_4K.zip", sha256: "a".repeat(64), size_bytes: 42 }] });

it("binds every preview and export receipt to the exact explicit selection", () => {
  expect(localPublicationPreview(publicationPreviewDto(), selected).canPrepare).toBe(true);
  expect(localPublicationJob(completed(), selected).archives[0].sizeBytes).toBe(42);
  const different = ["00000000-0000-4000-8000-000000000099"];
  expect(() => localPublicationPreview(publicationPreviewDto(), different)).toThrow();
  expect(() => localPublicationJob(completed(), different)).toThrow();
  expect(() => localPublicationPreview(publicationPreviewDto(), [selected[0],selected[0]])).toThrow();
});
it("does not accept a green review with errors or missing export data", () => {
  const preview = publicationPreviewDto(); preview.items[0].errors = ["MATERIAL_DONE_REQUIRED"];
  expect(() => localPublicationPreview(preview, selected)).toThrow();
  expect(() => localPublicationPreview({ ...publicationPreviewDto(), items: [{ ...publicationPreviewDto().items[0], row:null }] }, selected)).toThrow();
  expect(() => localPublicationPreview({ ...publicationPreviewDto(), preview_hash:"bad" }, selected)).toThrow();
});
it("accepts draft missing-identity diagnostics while rejecting null identities in publishable rows", () => {
  const source = publicationPreviewDto();
  const draft = { ...source, can_prepare: false, items: [{ ...source.items[0], identity_name: null, row: null, errors: ["MATERIAL_IDENTITY_INCOMPLETE", "FOLDER_REQUIRED"] }] };
  const parsed = localPublicationPreview(draft, selected);
  expect(parsed.canPrepare).toBe(false); expect(parsed.items[0].identity).toBeNull();
  expect(parsed.items[0].errors).toContain("MATERIAL_IDENTITY_INCOMPLETE");
  expect(() => localPublicationPreview({ ...draft, items: [{ ...draft.items[0], errors: [] }] }, selected)).toThrow();
  expect(() => localPublicationPreview({ ...draft, items: [{ ...draft.items[0], row: { ...source.items[0].row, identity_name: null } }] }, selected)).toThrow();
});
it.each([
  { output_path:null }, { csv_name:null }, { archives:[] }, { error_code:"FAILED" },
  { archives:[{ name:"../file.zip", sha256:"a".repeat(64), size_bytes:42 }] },
  { archives:[{ name:"file.zip", sha256:"bad", size_bytes:42 }] },
  { archives:[{ name:"file.zip", sha256:"a".repeat(64), size_bytes:-1 }] },
  { status:"RUNNING", published:true }, { status:"UNRECOGNIZED" },
])("rejects incomplete or malformed export receipt %j", override => {
  expect(() => localPublicationJob({ ...completed(), ...override }, selected)).toThrow();
});
it("handles a cancelled native picker and rejects partial destination capabilities", async () => {
  const request = vi.spyOn(transport,"request").mockResolvedValueOnce({ destination_token:null, destination_path:null });
  expect(await localPublicationClient.destination()).toBeNull();
  request.mockResolvedValueOnce({ destination_token:"opaque", destination_path:null });
  await expect(localPublicationClient.destination()).rejects.toThrow();
  request.mockResolvedValueOnce({ destination_token:"opaque", destination_path:"C:\\Exports" });
  expect(await localPublicationClient.destination()).toEqual({ token:"opaque", path:"C:\\Exports" });
});
it("rejects a receipt for a different job and a partial Published acknowledgement", async () => {
  const request = vi.spyOn(transport,"request").mockResolvedValueOnce({ ...completed(), id:"00000000-0000-4000-8000-000000000099" });
  await expect(localPublicationClient.detail(publicationId, selected)).rejects.toThrow();
  request.mockResolvedValueOnce({ published:true, material_ids:[] });
  await expect(localPublicationClient.markPublished(publicationId, selected, publicationId)).rejects.toThrow();
});
it("binds create recovery to its request key and preserves scoped packaging issues", async () => {
  vi.spyOn(transport,"request").mockResolvedValueOnce({ ...completed(), id:"00000000-0000-4000-8000-000000000099" });
  await expect(localPublicationClient.create({ material_ids:selected, expected_preview_hash:"a".repeat(64), destination_token:"opaque", idempotency_key:publicationId })).rejects.toThrow();
  const failure = { ...completed(), status:"FAILED", error_code:"LOCAL_EXPORT_MATERIAL_FILES_INVALID", output_path:null,
    issues:[{ material_id:materialDto.id, issues:[{ code:"MAP_MISSING", path:"4K/DIFFUSE.png" }] }] };
  expect(localPublicationJob(failure, selected).issues[0].issues[0]).toEqual({ code:"MAP_MISSING", path:"4K/DIFFUSE.png" });
  expect(() => localPublicationJob({ ...failure, issues:[{ ...failure.issues[0], material_id:publicationId }] }, selected)).toThrow();
});
