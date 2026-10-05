import { afterEach, expect, it, vi } from "vitest";
import { request } from "./client";
import { materialDeletionClient, type MaterialDeletionConfirmation } from "./materialDeletionClient";

vi.mock("./client", () => ({ request: vi.fn() }));
afterEach(() => vi.resetAllMocks());

const material = "00000000-0000-4000-8000-000000000020", operation = "00000000-0000-4000-8000-000000000021";
const body: MaterialDeletionConfirmation = { materials: [{ id: material, expected_updated_at: "2026-10-05T12:00:00Z" }],
  mode: "RECORD_ONLY", confirmed: true, idempotency_key: operation, expected_proposal_hash: "a".repeat(64) };
const item = { material_id: material, material_name: "DRAFT", identity: null, status: "COMPLETED", error_code: null };
const completed = { id: operation, mode: "RECORD_ONLY", status: "COMPLETED", deleted_count: 1, quarantine_retained: false, items: [item] };

it("preserves the exact reviewed deletion packet for repeat requests and accepts drafts", async () => {
  vi.mocked(request).mockResolvedValue(completed);
  expect(await materialDeletionClient.apply(body)).toEqual(completed);
  expect(await materialDeletionClient.apply(body)).toEqual(completed);
  expect(request).toHaveBeenNthCalledWith(1, "/material-deletions", "POST", body);
  expect(request).toHaveBeenNthCalledWith(2, "/material-deletions", "POST", body);
});

it("looks up durable recovery by frozen selected IDs and resumes an explicit operation", async () => {
  const pending = { ...completed, status: "RECOVERY_REQUIRED", deleted_count: 0,
    items: [{ ...item, status: "RECOVERY_REQUIRED", error_code: "MATERIAL_DELETE_IO_INTERRUPTED" }] };
  vi.mocked(request).mockResolvedValueOnce({ items: [pending] }).mockResolvedValueOnce(completed);
  expect(await materialDeletionClient.pending([material])).toEqual([pending]);
  expect(request).toHaveBeenNthCalledWith(1, `/material-deletions?material_ids=${material}`);
  await materialDeletionClient.resume(operation);
  expect(request).toHaveBeenNthCalledWith(2, `/material-deletions/${operation}/resume`, "POST", { confirmed: true });
});

it("accepts a bounded reviewed draft plan and rejects duplicate or mismatched counts", async () => {
  const plan = { proposal_hash: "a".repeat(64), can_apply: true, mode: "RECORD_ONLY", total: 1, warnings: ["AUDIT_RETAINED"],
    items: [{ material_id: material, material_name: "DRAFT", identity: null, folder_path: null, file_count: null, issues: [] }] };
  vi.mocked(request).mockResolvedValue(plan);
  expect(await materialDeletionClient.plan(body)).toEqual(plan);
  vi.mocked(request).mockResolvedValue({ ...plan, total: 2 });
  await expect(materialDeletionClient.plan(body)).rejects.toThrow("Invalid deletion selection");
  vi.mocked(request).mockResolvedValue({ ...plan, total: 2, items: [...plan.items, ...plan.items] });
  await expect(materialDeletionClient.plan(body)).rejects.toThrow("Invalid deletion selection");
});

it("rejects contradictory terminal outcomes and negative deletion counts", async () => {
  vi.mocked(request).mockResolvedValue({ ...completed, deleted_count: -1 });
  await expect(materialDeletionClient.get(operation)).rejects.toThrow("Invalid deletion count");
  vi.mocked(request).mockResolvedValue({ ...completed, status: "PARTIAL" });
  await expect(materialDeletionClient.get(operation)).rejects.toThrow("Invalid deletion outcome");
  vi.mocked(request).mockResolvedValue({ ...completed, status: "REJECTED" });
  await expect(materialDeletionClient.get(operation)).rejects.toThrow("Invalid deletion outcome");
});

it("rejects a malformed operation ID or unbounded pending selection before a request", async () => {
  await expect(materialDeletionClient.resume("../other")).rejects.toThrow();
  await expect(materialDeletionClient.pending(Array.from({ length: 101 }, () => material))).rejects.toThrow();
  expect(request).not.toHaveBeenCalled();
});
