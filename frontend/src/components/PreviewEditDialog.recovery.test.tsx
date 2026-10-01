import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PreviewEditDialog } from "./PreviewEditDialog";
import { MaterialsTable } from "./MaterialsTable";
import { previewEditClient, type PreviewEditPlan, type PreviewEditResult } from "../api/previewEditClient";
import { ApiError } from "../api/errors";
import { GalleryStore } from "../api/galleryStore";
import { mockApiClient } from "../api/client";
import { materialFromDto } from "../api/materialDto";
import { materialDto, processorDto } from "../test/materialFixtures";
import { SessionContext } from "../auth/context";
import { requestNavigation } from "../navigationGuard";

const material = { ...materialFromDto(materialDto), folderPath: "TEST/TEST_0001_SAMPLE_K03" };
const second = { ...material, id: "50000000-0000-4000-8000-000000000002", materialName: "SECOND" };
const plan: PreviewEditPlan = { proposal_hash: "a".repeat(64), can_apply: true, total_renames: 0, total_deletes: 1,
  items: [{ material_id: material.id, identity: material.technicalIdentity, rename_count: 0, delete_count: 1,
    changes: [{ from: "SPHERE_1.png", to: null }], issues: [] }] };
const completed: PreviewEditResult = { id: "00000000-0000-4000-8000-000000000099", status: "COMPLETED",
  items: [{ material_id: material.id, status: "COMPLETED", renamed: 0, deleted: 1, error_code: null }] };

beforeEach(() => {
  localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  vi.spyOn(previewEditClient, "pending").mockResolvedValue([]);
  vi.spyOn(previewEditClient, "plan").mockResolvedValue(plan);
  vi.spyOn(previewEditClient, "apply").mockResolvedValue(completed);
});
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

it.each([403, 409])("retains the exact request after a first %i that may follow the server commit", async status => {
  vi.mocked(previewEditClient.apply).mockRejectedValueOnce(new ApiError(status, "The saved operation could not finish."));
  const changed = vi.fn(), close = vi.fn();
  render(<PreviewEditDialog selection={{ materials: [material], action: "DELETE", filename: "SPHERE_1.png" }} onChanged={changed} onClose={close} />);
  fireEvent.click(await screen.findByRole("button", { name: "Confirm rename / delete previews" }));
  const recover = await screen.findByRole("button", { name: "Recover preview changes" });
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(requestNavigation("/orders")).toBe(false);
  expect(changed).not.toHaveBeenCalled();
  fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
  expect(close).not.toHaveBeenCalled();
  const original = vi.mocked(previewEditClient.apply).mock.calls[0][0];
  fireEvent.click(recover);
  await screen.findByText("Preview changes completed.");
  expect(vi.mocked(previewEditClient.apply).mock.calls[1][0]).toEqual(original);
  expect(changed).toHaveBeenCalledOnce();
  expect(requestNavigation("/orders")).toBe(true);
});

it("keeps the table preview result and parent busy state until Close triggers the refresh", async () => {
  const refresh = vi.fn(), store = new GalleryStore();
  vi.spyOn(store, "listing").mockImplementation(() => new Promise(() => {}));
  vi.mocked(previewEditClient.apply).mockResolvedValue({ ...completed, status: "PARTIAL",
    items: [...completed.items, { material_id: second.id, status: "REJECTED", renamed: 0, deleted: 0, error_code: "PREVIEW_SOURCE_CHANGED" }] });
  function Parent() {
    const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
    return <><output aria-label="Parent busy">{String(busy)}</output>{loading ? <p>Reloading material records</p> : <MaterialsTable
      materials={[material, second]} store={store} client={mockApiClient} projects={[]} brands={[]} users={[]}
      navigate={vi.fn()} onBusyChange={setBusy} refresh={() => { refresh(); setLoading(true); }} />}</>;
  }
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}><Parent /></SessionContext.Provider>);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all visible materials" }));
  fireEvent.click(screen.getByRole("button", { name: "Edit previews" }));
  fireEvent.change(screen.getByLabelText("Delete previews containing text"), { target: { value: "SPHERE" } });
  fireEvent.click(await screen.findByRole("button", { name: "Confirm rename / delete previews" }));
  await screen.findByText("Review the results. Some materials could not be changed.");
  expect(screen.getByRole("region", { name: "Preview edit result" })).toHaveTextContent("PREVIEW_SOURCE_CHANGED");
  expect(screen.getByLabelText("Parent busy")).toHaveTextContent("true");
  expect(refresh).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await screen.findByText("Reloading material records");
  await waitFor(() => expect(screen.getByLabelText("Parent busy")).toHaveTextContent("false"));
  expect(refresh).toHaveBeenCalledOnce();
});
