import { request } from "./client";
import { companyChange } from "./companyHistoryClient";
import { record, string, uuid } from "./dto";
import { notionPageId, type NotionComparison, type NotionField } from "./notionClient";

export interface NotionAdoptionRequest {
  request_key: string;
  expected_page_id: string;
  expected_local_sha256: string;
  expected_observation_sha256: string;
  selected_fields: NotionField[];
  reason: string;
}
export function adoptionRequest(comparison: NotionComparison, fields: NotionField[], reason: string, key: string = crypto.randomUUID()): NotionAdoptionRequest {
  reason = reason.trim();
  if (!fields.length || fields.length > 6 || new Set(fields).size !== fields.length
      || fields.some((field) => !comparison.rows.some((row) => row.field === field && row.changed))
      || !reason || reason.length > 2000 || [...reason].some((char) => char.charCodeAt(0) < 32 && !"\n\r\t".includes(char) || char.charCodeAt(0) === 127)
      || !notionPageId(key)) throw new Error("Review selected changes and their reason");
  return { request_key: uuid(key), expected_page_id: comparison.pageId, expected_local_sha256: comparison.localHash,
    expected_observation_sha256: comparison.observationHash, selected_fields: [...fields].sort(), reason };
}
export function notionAdoptionResult(input: unknown, comparison: NotionComparison, actorId: string, body: NotionAdoptionRequest) {
  const value = record(input), raw = record(value.event), source = record(raw.source);
  const event = companyChange(raw, comparison.companyId);
  if (uuid(value.request_key) !== body.request_key || event.actorId !== uuid(actorId) || event.action !== "NOTION_ADOPTED"
      || event.reason !== body.reason || !event.before || event.changed.sort().join() !== body.selected_fields.join()
      || notionPageId(event.before.notion_page_id) !== comparison.pageId
      || source.page_id !== comparison.pageId || source.data_source_id !== comparison.dataSourceId || source.database_id !== comparison.databaseId
      || source.last_edited_time !== comparison.editedAt || source.mapping_sha256 !== comparison.mappingHash || source.observation_sha256 !== comparison.observationHash
      || !Array.isArray(source.selected_fields) || source.selected_fields.map(string).join() !== body.selected_fields.join()
      || Object.keys(source).sort().join() !== ["page_id", "data_source_id", "database_id", "last_edited_time", "mapping_sha256", "observation_sha256", "selected_fields"].sort().join()
      || comparison.rows.some((row) => event.before![row.field] !== row.current)
      || body.selected_fields.some((field) => {
        const row = comparison.rows.find((row) => row.field === field);
        return !row || event.before![field] !== row.current || event.after[field] !== row.observed;
      })) throw new Error("Unrelated or invalid Notion adoption evidence");
  return event;
}
function validate(comparison: NotionComparison, body: NotionAdoptionRequest) {
  const expected = adoptionRequest(comparison, body.selected_fields, body.reason, body.request_key);
  if (JSON.stringify(expected) !== JSON.stringify(body)) throw new Error("Changed adoption request");
}
export const notionAdoptionClient = {
  async adopt(comparison: NotionComparison, actorId: string, body: NotionAdoptionRequest) {
    validate(comparison, body);
    return notionAdoptionResult(await request(`/companies/${uuid(comparison.companyId)}/notion-adopt`, "POST", body), comparison, actorId, body);
  },
  async recover(comparison: NotionComparison, actorId: string, body: NotionAdoptionRequest) {
    validate(comparison, body);
    return notionAdoptionResult(await request(`/companies/${uuid(comparison.companyId)}/notion-adoptions/${uuid(body.request_key)}`), comparison, actorId, body);
  },
};
