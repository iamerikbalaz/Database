import { afterEach, expect, it, vi } from "vitest";
import { identityClient } from "./identityClient";
import { materialDto } from "../test/materialFixtures";
import { setSessionToken } from "../auth/sessionTransport";

afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
it("accepts the complete metadata rename plan including unrecognized resolution filenames", async () => {
  const source = { ...materialDto, material_id: materialDto.id, folder_path: "BRAND/BRAND_9999_OLD_G03" };
  const target = { ...source, material_name: "NEW", folder_path: "BRAND/BRAND_9999_NEW_G03" };
  const fields = ["FOLDER", "MANUFACTURER", "PRODUCT_NAME", "CATEGORY", "PRODUCT_NUMBER", "BASE_NAME", "TEXTURE_SIZE_SOURCE", "COLOR.measured_from", "SOURCE.SBS", "RESOLUTIONS.UNRECOGNIZED"];
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ source_context: source, target_context: target, generation: 1, reserves_number: false,
    proposal_hash: "b".repeat(64), worker_plan: { schema_version: 1, planner_version: "identity-plan-1", plan_hash: "a".repeat(64), source_revision_hash: "c".repeat(64),
      source_path: source.folder_path, target_path: target.folder_path, ready: true, errors: [], warnings: [], changes: [],
      metadata: { before_hash: "d".repeat(64), after_hash: "e".repeat(64), changed_fields: fields } } }))));
  setSessionToken("t".repeat(43));
  const result = await identityClient.plan(materialDto.id, { target_brand_id: materialDto.published_brand_id, target_parent: "BRAND", main_category_code: "G03", material_name: "NEW" });
  expect(result.ready).toBe(true);
  expect(result.metadata.fields).toEqual(fields);
});
