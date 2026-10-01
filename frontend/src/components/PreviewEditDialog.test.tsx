import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PreviewEditDialog } from "./PreviewEditDialog";
import { previewEditClient, type PreviewEditPlan, type PreviewEditResult } from "../api/previewEditClient";
import { materialFromDto } from "../api/materialDto";
import { materialDto } from "../test/materialFixtures";
import { ApiError } from "../api/errors";
import { requestNavigation } from "../navigationGuard";

const material = materialFromDto(materialDto);
const plan: PreviewEditPlan = { proposal_hash: "a".repeat(64), can_apply: true, total_renames: 1, total_deletes: 1,
  items: [{ material_id: material.id, identity: material.technicalIdentity, rename_count: 1, delete_count: 1,
    changes: [{ from: "OLD_1.png", to: "NEW_1.png" }, { from: "REMOVE_2.png", to: null }], issues: [] }] };
const completed: PreviewEditResult = { id: "00000000-0000-4000-8000-000000000099", status: "COMPLETED",
  items: [{ material_id: material.id, status: "COMPLETED", renamed: 1, deleted: 1, error_code: null }] };
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  vi.spyOn(previewEditClient, "pending").mockResolvedValue([]);
  vi.spyOn(previewEditClient, "plan").mockResolvedValue(plan);
  vi.spyOn(previewEditClient, "apply").mockResolvedValue(completed);
  vi.spyOn(previewEditClient, "resume").mockResolvedValue(completed);
});
afterEach(() => vi.restoreAllMocks());
function mount(action: "BULK" | "DELETE" | "RENAME" = "BULK") {
  const close = vi.fn(), changed = vi.fn();
  render(<PreviewEditDialog selection={{ materials: [material], action, ...(action === "BULK" ? {} : { filename: "SPHERE_1.png" }) }} onClose={close} onChanged={changed} />);
  return { close, changed };
}
function fillBulk() {
  fireEvent.change(screen.getByLabelText("Replace text"), { target: { value: "OLD" } });
  fireEvent.change(screen.getByLabelText("With"), { target: { value: "NEW" } });
  fireEvent.change(screen.getByLabelText("Delete previews containing text"), { target: { value: "REMOVE" } });
}
it("reviews combined replace/delete counts and source filenames before sending the frozen confirmed operation", async () => {
  const { changed } = mount(); fillBulk();
  const confirm = await screen.findByRole("button", { name: "Confirm rename / delete previews" });
  expect(screen.getByRole("region", { name: "Preview changes" })).toHaveTextContent("1 previews to rename · 1 previews to delete");
  expect(previewEditClient.apply).not.toHaveBeenCalled();
  fireEvent.click(confirm);
  await screen.findByText("Preview changes completed.");
  expect(previewEditClient.apply).toHaveBeenCalledWith({ materials: [{ id: material.id, expected_updated_at: material.updatedAt }], action: "BULK", find: "OLD", replace: "NEW", delete_containing: "REMOVE", case_sensitive: true, confirmed: true, expected_proposal_hash: plan.proposal_hash, idempotency_key: expect.any(String) });
  expect(changed).toHaveBeenCalledOnce();
});
it("allows cancellation of a single deletion without mutating files", async () => {
  const { close } = mount("DELETE");
  await screen.findByRole("button", { name: "Confirm rename / delete previews" });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(close).toHaveBeenCalledOnce(); expect(previewEditClient.apply).not.toHaveBeenCalled();
});
it("omits replacement fields for a deletion-only bulk edit and refuses stale counts after input changes", async () => {
  mount(); fireEvent.change(screen.getByLabelText("Delete previews containing text"), { target: { value: "DELETE" } });
  await screen.findByRole("button", { name: "Confirm rename / delete previews" });
  expect(previewEditClient.plan).toHaveBeenLastCalledWith(expect.objectContaining({ action: "BULK", delete_containing: "DELETE" }));
  expect(vi.mocked(previewEditClient.plan).mock.calls.at(-1)![0]).not.toHaveProperty("replace");
  fireEvent.change(screen.getByLabelText("Delete previews containing text"), { target: { value: "CHANGED" } });
  expect(screen.getByRole("button", { name: "Confirm rename previews" })).toBeDisabled();
});
it("retains exactly the same confirmation and blocks leaving after a lost reply and later 403", async () => {
  vi.mocked(previewEditClient.apply).mockRejectedValueOnce(new TypeError("Lost reply")).mockRejectedValueOnce(new ApiError(403, "Access changed")).mockResolvedValue(completed);
  mount(); fillBulk(); fireEvent.click(await screen.findByRole("button", { name: "Confirm rename / delete previews" }));
  fireEvent.click(await screen.findByRole("button", { name: "Recover preview changes" }));
  await waitFor(() => expect(previewEditClient.apply).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByRole("button", { name: "Recover preview changes" })).toBeEnabled());
  expect(requestNavigation("/orders")).toBe(false); expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Recover preview changes" }));
  await screen.findByText("Preview changes completed.");
  const calls = vi.mocked(previewEditClient.apply).mock.calls;
  expect(calls[1]).toEqual(calls[0]); expect(calls[2]).toEqual(calls[0]); expect(requestNavigation("/orders")).toBe(true);
});
it("recovers an operation discovered after reload without making a new plan", async () => {
  vi.mocked(previewEditClient.pending).mockResolvedValue([{ ...completed, status: "RUNNING", items: [{ ...completed.items[0], status: "RUNNING" }] }]);
  mount("DELETE");
  fireEvent.click(await screen.findByRole("button", { name: "Recover preview changes" }));
  await screen.findByText("Preview changes completed.");
  expect(previewEditClient.resume).toHaveBeenCalledWith(completed.id); expect(previewEditClient.apply).not.toHaveBeenCalled(); expect(previewEditClient.plan).not.toHaveBeenCalled();
});
it("shows collisions and disables confirmation for a blocked plan", async () => {
  vi.mocked(previewEditClient.plan).mockResolvedValue({ ...plan, can_apply: false, items: [{ ...plan.items[0], issues: [{ code: "PREVIEW_NAME_COLLISION", message: "Target already exists." }] }] });
  mount("RENAME"); fireEvent.change(screen.getByLabelText("New filename"), { target: { value: "EXISTS.png" } });
  await screen.findByText("Target already exists.");
  expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Confirm rename / delete previews" })).toBeDisabled();
});
