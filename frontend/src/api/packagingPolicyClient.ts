import { request } from "./client";
import { boolean, record, string, uuid } from "./dto";
import { review } from "./reviewClient";
import { technicalClient } from "./technicalClient";

export const policyValues = ["LEGACY_BEFORE_2026_03_04", "CURRENT_ON_OR_AFTER_2026_03_04"] as const;
export type ZipPolicy = typeof policyValues[number];
export function policyLabel(value: ZipPolicy) {
  return value === policyValues[0] ? "Legacy · normalize ZIP dates to 1 January 2026" : "Current · retain packaging dates";
}
function policy(value: unknown): ZipPolicy {
  if (!policyValues.includes(value as ZipPolicy)) throw new Error("Invalid ZIP policy");
  return value as ZipPolicy;
}
function positive(value: unknown) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) throw new Error("Invalid policy revision");
  return value;
}
function digest(value: unknown) {
  const result = string(value); if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid policy proof"); return result;
}
function bounded(value: unknown, limit: number) {
  const result = string(value); if (!result || result.length > limit) throw new Error("Invalid policy text"); return result;
}
export function policyFromDto(value: unknown) {
  const data = record(value), revision = positive(data.revision);
  const previousId = data.previous_id === null ? null : uuid(data.previous_id);
  const inventoryId = data.inventory_id === null ? null : uuid(data.inventory_id);
  if ((revision === 1 ? data.kind !== "INITIAL" || previousId !== null || inventoryId === null :
    data.kind !== "OVERRIDE" || previousId === null || inventoryId !== null)) throw new Error("Invalid policy ancestry");
  return { id: uuid(data.id), materialId: uuid(data.material_id), revision, previousId, inventoryId,
    policy: policy(data.policy), storageTimezone: bounded(data.storage_timezone, 100), actorId: uuid(data.actor_id),
    reason: bounded(data.reason, 2000), evidenceHash: digest(data.evidence_hash), createdAt: string(data.created_at) };
}
export type PolicyDecision = ReturnType<typeof policyFromDto>;
function decision(input: unknown, id: string) {
  const data = policyFromDto(input); if (data.materialId !== id) throw new Error("Wrong policy material"); return data;
}
export interface PolicySelection {
  idempotency_key: string; expected_generation: number; expected_revision_hash: string; expected_inventory_id: string; reason: string;
}
export interface PolicyOverride {
  idempotency_key: string; expected_policy_id: string; policy: ZipPolicy; expected_preview_hash: string; reason: string;
}
function previewFromDto(input: unknown, id: string, expectedId: string, proposed: ZipPolicy) {
  const data = record(input), current = record(data.current), effects = record(data.effects);
  if (uuid(data.material_id) !== id || uuid(current.id) !== expectedId || policy(data.proposed_policy) !== proposed ||
      effects.invalidate_current_approvals !== true || effects.preserve_existing_artifacts !== true ||
      effects.source_files_modified !== false || effects.published_update_required !== data.is_published) throw new Error("Inconsistent policy preview");
  return { currentId: expectedId, currentPolicy: policy(current.policy), currentRevision: positive(current.revision),
    proposedPolicy: proposed, storageTimezone: bounded(current.storage_timezone, 100), identity: string(data.technical_identity),
    isPublished: boolean(data.is_published), review: review(data.review), previewHash: digest(data.preview_hash) };
}
export type PolicyPreview = ReturnType<typeof previewFromDto>;
const path = (id: string) => "/materials/" + uuid(id) + "/packaging-policy";
export const packagingPolicyClient = {
  async current(id: string) {
    const value = record(await request(path(id)));
    return value.current === null ? null : decision(value.current, id);
  },
  async observed(id: string) {
    const [source, check] = await Promise.all([request("/materials/" + uuid(id) + "/inventory"), technicalClient.current(id)]);
    const data = record(source), state = review(data.review);
    if (state.failureCode || !state.inventoryId || !state.revisionHash || !check.validation?.canApprove ||
        check.review.generation !== state.generation || check.review.inventoryId !== state.inventoryId ||
        check.review.revisionHash !== state.revisionHash || data.inventory === null) return null;
    const inventory = record(data.inventory);
    return { review: state, policy: policy(inventory.policy), master: bounded(inventory.master_resolution, 20),
      modifiedAt: bounded(inventory.master_last_modified_at, 64) };
  },
  async select(id: string, payload: PolicySelection) {
    return decision(record(await request(path(id) + "/select", "POST", payload)).current, id);
  },
  async preview(id: string, current: PolicyDecision, proposed: ZipPolicy) {
    return previewFromDto(await request(path(id) + "/override-preview", "POST", {
      expected_policy_id: current.id, policy: proposed,
    }), id, current.id, proposed);
  },
  async override(id: string, payload: PolicyOverride) {
    return decision(record(await request(path(id) + "/override", "POST", payload)).current, id);
  },
  async history(id: string, before?: number) {
    const data = record(await request(path(id) + "/history" + (before === undefined ? "" : "?before=" + positive(before))));
    if (!Array.isArray(data.items) || data.items.length > 20) throw new Error("Invalid policy history");
    const items = data.items.map((item) => decision(item, id));
    if (items.some((item, index) => (before !== undefined && item.revision >= before) ||
        (index > 0 && item.revision >= items[index - 1].revision))) throw new Error("Unordered policy history");
    const nextBefore = data.next_before === null ? null : positive(data.next_before);
    if (nextBefore !== null && items.at(-1)?.revision !== nextBefore) throw new Error("Invalid policy cursor");
    return { items, nextBefore };
  },
};
