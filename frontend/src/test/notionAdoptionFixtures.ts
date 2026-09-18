import type { NotionAdoptionRequest } from "../api/notionAdoptionClient";
import { companyChangeDto } from "./companyHistoryFixtures";
import { notionComparisonDto, notionCompanyId, notionId } from "./notionFixtures";
import { processorDto } from "./materialFixtures";

export function notionAdoptionDto(body: NotionAdoptionRequest, companyId = notionCompanyId, actorId = processorDto.id) {
  const comparison = notionComparisonDto(companyId, notionId);
  const original = companyChangeDto(1, companyId);
  const before = { ...original.after, notion_page_id: notionId };
  const after = { ...before };
  for (const field of body.selected_fields) Object.assign(after, { [field]: comparison.fields.find((row) => row.field === field)!.observed });
  return { request_key: body.request_key, event: { ...original, action: "NOTION_ADOPTED", actor_id: actorId,
    before, after, reason: body.reason, source: { ...comparison.source, selected_fields: [...body.selected_fields] } } };
}
