import { expect, it } from "vitest";
import { materialFromDto, parseMaterial } from "./materialDto";
import { materialDto } from "../test/materialFixtures";

const draft = { ...materialDto, is_draft: true, published_brand_id: null, project_id: null, assigned_processor_id: null,
  main_category_code: null, sequence_number: null, technical_identity: null };
it("preserves real missing draft identity fields as null", () => {
  const value = materialFromDto(parseMaterial(draft));
  expect(value).toMatchObject({ isDraft: true, publishedBrandId: null, assignedProcessorId: null, mainCategoryCode: null, sequenceNumber: null, technicalIdentity: null });
});
it.each(["published_brand_id", "main_category_code", "sequence_number", "technical_identity"])("does not relax complete identity validation for %s", field => {
  expect(() => parseMaterial({ ...materialDto, [field]: null })).toThrow();
});
it("rejects a draft with a folder or a fabricated sequence", () => {
  expect(() => parseMaterial({ ...draft, folder_path: "SAFE/FOLDER" })).toThrow();
  expect(() => parseMaterial({ ...draft, sequence_number: 1 })).toThrow();
});
