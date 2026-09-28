import { afterEach, expect, it, vi } from "vitest";
import { materialLocalClient } from "./materialLocalClient";
import { materialDto } from "../test/materialFixtures";
import { materialFromDto, parseMaterial } from "./materialDto";

const material = materialFromDto(materialDto);
const result = { material_id: material.id, status: "NOT_CHECKED", checked_at: material.updatedAt, updated_at: material.updatedAt,
  profile: "BASIC_V1", complete: false, report: "Preliminary inspection. Final rules not configured.", issues: [] };
afterEach(() => vi.unstubAllGlobals());

it("requests only the explicit selected IDs and versions, with a persisted report outcome", async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ items: [result], report: result.report, report_path: "C:\\Reports\\check.txt", report_opened: true })));
  vi.stubGlobal("fetch", fetch);
  expect(await materialLocalClient.checkMany([material])).toMatchObject({ items: [{ materialId: material.id, status: "NOT_CHECKED", complete: false }], reportPath: "C:\\Reports\\check.txt", reportOpened: true });
  expect(fetch).toHaveBeenCalledWith("/api/materials/check-data", expect.objectContaining({ method: "POST",
    body: JSON.stringify({ materials: [{ id: material.id, expected_updated_at: material.updatedAt }], open_report: true }) }));
});

it("allows publication's optional check to save the report without opening an editor", async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ items: [result], report: result.report, report_path: null, report_opened: false })));
  vi.stubGlobal("fetch", fetch);
  const checked = await materialLocalClient.checkMany([material], false);
  expect(checked.reportPath).toBeNull(); expect(checked.report).toContain("Preliminary");
  expect(fetch).toHaveBeenCalledWith("/api/materials/check-data", expect.objectContaining({ body: expect.stringContaining('"open_report":false') }));
});

it.each([{ materials: [] }, { materials: [material, material] }, { materials: Array.from({ length: 101 }, () => material) }])("rejects empty, duplicate or oversized selection before sending", async ({ materials }) => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  await expect(materialLocalClient.checkMany(materials)).rejects.toThrow(); expect(fetch).not.toHaveBeenCalled();
});

it.each([
  { items: [] }, { items: [{ ...result, status: "VALID" }] },
  { items: [{ ...result, material_id: "50000000-0000-4000-8000-000000000002" }] },
  { items: [{ ...result, complete: "false" }] },
])("rejects inconsistent bulk check responses", async change => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ report: result.report, report_path: null, report_opened: false, ...change }))));
  await expect(materialLocalClient.checkMany([material])).rejects.toThrow();
});

it("maps the persisted check separately from human Checked and technical validation", () => {
  const parsed = materialFromDto(parseMaterial({ ...materialDto, checked_status: "OK", validation_status: "VALID", automatic_file_check_status: "ISSUES",
    automatic_file_checked_at: result.checked_at, automatic_file_check_profile: "BASIC_V1", automatic_file_check_complete: false }));
  expect(parsed).toMatchObject({ checkedStatus: "OK", validationStatus: "VALID", automaticFileCheckStatus: "ISSUES", automaticFileCheckProfile: "BASIC_V1", automaticFileCheckComplete: false });
  expect(parsed).not.toHaveProperty("automaticFileCheckReport");
  expect(() => parseMaterial({ ...materialDto, automatic_file_check_status: "VALID" })).toThrow();
});
