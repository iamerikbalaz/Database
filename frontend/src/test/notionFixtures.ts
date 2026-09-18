export const notionCompanyId = "11111111-1111-4111-8111-111111111111";
export const notionId = "88888888-8888-8888-8888-888888888888";
export const notionConfig = () => ({ enabled: true, direction: "READ_ONLY", resource: "COMPANY", mapped_fields: ["country", "name", "website"] });
export function notionComparisonDto(companyId = notionCompanyId, pageId = notionId) {
  return { company_id: companyId, direction: "READ_ONLY", local_sha256: "a".repeat(64), source: {
    page_id: pageId, data_source_id: "22222222-2222-4222-8222-222222222222", database_id: "33333333-3333-4333-8333-333333333333",
    last_edited_time: "2026-09-18T12:00:00.000Z", mapping_sha256: "b".repeat(64), observation_sha256: "c".repeat(64),
  }, fields: [{ field: "country", current: "CZ", observed: null, changed: true },
    { field: "name", current: "Synthetic company", observed: "Synthetic česká company", changed: true },
    { field: "website", current: "https://example.invalid/", observed: "https://example.invalid/", changed: false }] };
}
