import { afterEach, expect, it, vi } from "vitest";
import { httpApiClient } from "./client";
import { internalUserFromDto, materialFromDto, parseInternalUser, parseMaterial } from "./materialDto";
import { metadataDto, materialDto, preflightDto, processorDto, snapshotDto } from "../test/materialFixtures";
import { parsePublishedBrand } from "./dto";
import { materialBrand } from "../test/materialFixtures";
afterEach(() => vi.unstubAllGlobals());
it("validates and explicitly maps all material and user fields", () => {
  expect(materialFromDto(parseMaterial(materialDto))).toEqual({
    id: materialDto.id, projectId: materialDto.project_id, publishedBrandId: materialDto.published_brand_id,
    assignedProcessorId: materialDto.assigned_processor_id, materialName: "Crystal surface", mainCategoryCode: "G03",
    technicalIdentity: "LASVIT_9999_G03", sequenceNumber: 9999, folderPath: null, workflowStatus: "IN_PROGRESS",
    validationStatus: "NOT_CHECKED", publicationStatus: "NOT_PUBLISHED", isPublished: false,
    createdAt: materialDto.created_at, updatedAt: materialDto.updated_at,
  });
  expect(internalUserFromDto(parseInternalUser(processorDto))).toEqual({
    id: processorDto.id, displayName: processorDto.display_name, email: processorDto.email,
    role: "PROCESSOR", isActive: true, createdAt: processorDto.created_at, updatedAt: processorDto.updated_at,
  });
  expect(parsePublishedBrand(materialBrand).next_sequence_number).toBe(10000);
});
it.each([{ sequence_number: 0 }, { sequence_number: 10000 }, { sequence_number: 1.5 }, { id: "wrong" }, { is_published: "false" }, { workflow_status: "unknown" }, { validation_status: "unknown" }, { publication_status: "unknown" }, { folder_path: undefined }])("rejects malformed material response %j", (override) => {
  expect(() => parseMaterial({ ...materialDto, ...override })).toThrow();
});
it("rejects malformed user responses", () => {
  for (const override of [{ role: "unknown" }, { is_active: "true" }, { id: "wrong" }]) expect(() => parseInternalUser({ ...processorDto, ...override })).toThrow();
});
it("strips all managed fields at the HTTP boundary for POST and PATCH", async () => {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(materialDto)));
  vi.stubGlobal("fetch", fetchMock);
  await httpApiClient.createMaterial(materialDto);
  expect(fetchMock).toHaveBeenLastCalledWith("/api/materials", expect.objectContaining({ method: "POST", body: JSON.stringify({
    project_id: materialDto.project_id, published_brand_id: materialDto.published_brand_id,
    material_name: materialDto.material_name, main_category_code: materialDto.main_category_code,
    assigned_processor_id: materialDto.assigned_processor_id,
  }) }));
  await httpApiClient.updateMaterial(materialDto.id, materialDto);
  expect(fetchMock).toHaveBeenLastCalledWith("/api/materials/" + materialDto.id, expect.objectContaining({ method: "PATCH", body: JSON.stringify({
    project_id: materialDto.project_id, material_name: materialDto.material_name,
    main_category_code: materialDto.main_category_code, assigned_processor_id: materialDto.assigned_processor_id,
  }) }));
});
it("encodes search safely and does not send empty filters", async () => {
  const fetchMock = vi.fn(async () => new Response("[]")); vi.stubGlobal("fetch", fetchMock);
  await httpApiClient.getMaterials({ search: " A & B? ", project_id: "" });
  expect(fetchMock).toHaveBeenCalledWith("/api/materials?search=A+%26+B%3F", expect.any(Object));
});

it("uses the exact material-operation endpoints, bodies and explicit response mappings", async () => {
  const linked = { ...materialDto, folder_path: `library/${materialDto.technical_identity}` };
  const done = { ...linked, workflow_status: "DONE" };
  const routes: Record<string, unknown> = {
    [`POST /api/materials/${materialDto.id}/folder-preflight`]: {
      ...preflightDto,
      warnings: [{ code: "REVIEW", message: "Review metadata.", path: "C:\\server\\secret" }],
      raw_content: "must be ignored",
    },
    [`POST /api/materials/${materialDto.id}/folder-link`]: { material: linked, preflight: preflightDto },
    [`POST /api/materials/${materialDto.id}/mark-done`]: {
      material: done,
      metadata: { ...metadataDto, current_snapshot_id: snapshotDto.id, raw_content: "hidden" },
      snapshot: { ...snapshotDto, source_content: "hidden" },
      preflight: preflightDto,
    },
    [`GET /api/materials/${materialDto.id}/metadata`]: { ...metadataDto, raw_content: "hidden" },
    [`GET /api/materials/${materialDto.id}/metadata/snapshots`]: [{ ...snapshotDto, source_content: "hidden" }],
  };
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${path}`;
    return new Response(JSON.stringify(routes[key]), { status: key in routes ? 200 : 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  const path = `library/${materialDto.technical_identity}`;

  const preflight = await httpApiClient.preflightMaterialFolder(materialDto.id, path);
  expect(preflight).toMatchObject({ schemaVersion: 1, folderName: materialDto.technical_identity, identityMatches: true, canContinue: true });
  expect(preflight.warnings[0].path).toBeNull();
  const link = await httpApiClient.linkMaterialFolder(materialDto.id, path);
  const doneResult = await httpApiClient.markMaterialDone(materialDto.id);
  const currentMetadata = await httpApiClient.getMaterialMetadata(materialDto.id);
  const snapshots = await httpApiClient.getMaterialMetadataSnapshots(materialDto.id);
  expect(link).toMatchObject({ material: { folderPath: path } });
  expect(doneResult).toMatchObject({ material: { workflowStatus: "DONE" }, metadata: { currentSnapshotId: snapshotDto.id }, snapshot: { sequenceNumber: 1 } });
  expect(currentMetadata).toMatchObject({ status: "NOT_SCANNED", sourceFilename: null });
  expect(snapshots).toHaveLength(1);

  const calls = fetchMock.mock.calls;
  expect(calls[0][1]).toMatchObject({ method: "POST", body: JSON.stringify({ folder_path: path }) });
  expect(calls[1][1]).toMatchObject({ method: "POST", body: JSON.stringify({ folder_path: path }) });
  expect(calls[2][1]).toMatchObject({ method: "POST", body: undefined });
  expect(calls[3][1]).toMatchObject({ method: "GET", body: undefined });
  expect(calls[4][1]).toMatchObject({ method: "GET", body: undefined });
  expect(JSON.stringify([preflight, link, doneResult, currentMetadata, snapshots])).not.toMatch(
    /raw_content|source_content|rawContent|sourceContent|must be ignored|hidden/,
  );
});

it.each([
  ["preflight", { ...preflightDto, can_continue: undefined }],
  ["metadata", { ...metadataDto, warnings: undefined }],
  ["snapshot", { ...snapshotDto, sequence_number: 0 }],
])("rejects a malformed required %s response", async (kind, body) => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(kind === "snapshot" ? [body] : body))));
  const call = kind === "preflight"
    ? httpApiClient.preflightMaterialFolder(materialDto.id, "library/material")
    : kind === "metadata"
      ? httpApiClient.getMaterialMetadata(materialDto.id)
      : httpApiClient.getMaterialMetadataSnapshots(materialDto.id);
  await expect(call).rejects.toThrow();
});
