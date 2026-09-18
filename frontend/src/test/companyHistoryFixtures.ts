import { notionCompanyId } from "./notionFixtures";
export function companyChangeDto(version = 1, companyId = notionCompanyId) {
  const after = { id: companyId, name: "Synthetic company", legal_name: null, country: "CZ", address: null,
    website: "https://example.invalid/", vat_id: null, notion_page_id: null, is_active: true };
  return { id: `44444444-4444-4444-8444-${String(version).padStart(12, "0")}`, company_id: companyId,
    actor_id: "55555555-5555-4555-8555-555555555555", version, action: version === 1 ? "CREATED" : "UPDATED",
    before: version === 1 ? {} : { ...after, name: "Previous synthetic company" }, after,
    before_sha256: "a".repeat(64), after_sha256: "b".repeat(64), source: {}, reason: "", created_at: "2026-09-18T15:00:00Z" };
}
export function companyHistoryDto(count = 2, companyId = notionCompanyId) {
  const items = Array.from({ length: count }, (_, index) => companyChangeDto(count - index, companyId));
  return { company_id: companyId, items, next_cursor: null as string | null };
}
