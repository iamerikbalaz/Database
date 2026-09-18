import { uuid } from "./dto";

export const HISTORY_PAGE_SIZE = 100;
export function historyQuery(after: string | null) {
  return after === null ? "" : `?after=${uuid(after)}`;
}
export function historyPage<T extends { id: string }>(value: unknown, after: string | null, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length > HISTORY_PAGE_SIZE) throw new Error("Invalid history page");
  const items = value.map(parse);
  const ids = items.map((item) => uuid(item.id).toLowerCase());
  if (new Set(ids).size !== ids.length || (after !== null && ids.includes(uuid(after).toLowerCase()))) throw new Error("Repeated history record");
  return items;
}
