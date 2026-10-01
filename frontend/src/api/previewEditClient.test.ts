import { afterEach, expect, it, vi } from "vitest";
import { request } from "./client";
import { previewEditClient } from "./previewEditClient";
vi.mock("./client", () => ({ request: vi.fn() }));
afterEach(() => vi.resetAllMocks());
const material = "00000000-0000-4000-8000-000000000020", operation = "00000000-0000-4000-8000-000000000021";
it("looks up recovery by selected material IDs and parses running receipts", async () => {
  vi.mocked(request).mockResolvedValue({ items: [{ id: operation, status: "RUNNING", items: [{ material_id: material, status: "RUNNING", renamed: 0, deleted: 0, error_code: null }] }] });
  const result = await previewEditClient.pending([material]);
  expect(request).toHaveBeenCalledWith(`/material-preview-edits?material_ids=${material}`);
  expect(result[0].status).toBe("RUNNING"); expect(result[0].items[0].status).toBe("RUNNING");
});
it("requires valid server counts and confirmation outcomes", async () => {
  const body = { materials: [{ id: material, expected_updated_at: "2026-10-01T12:00:00Z" }], action: "DELETE" as const, filename: "SPHERE_1.png" };
  vi.mocked(request).mockResolvedValue({ proposal_hash: "a".repeat(64), can_apply: true, total_renames: 0, total_deletes: -1, items: [] });
  await expect(previewEditClient.plan(body)).rejects.toThrow("Invalid preview count");
  vi.mocked(request).mockResolvedValue({ id: operation, status: "UNKNOWN", items: [] });
  await expect(previewEditClient.resume(operation)).rejects.toThrow("Invalid preview operation status");
});
