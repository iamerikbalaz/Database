import { afterEach, expect, it, vi } from "vitest";
import { httpApiClient } from "./client";
import { internalUserFromDto, materialFromDto, parseInternalUser, parseMaterial } from "./materialDto";
import { materialDto, processorDto } from "../test/materialFixtures";
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
