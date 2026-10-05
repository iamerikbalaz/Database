import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialsTable } from "./MaterialsTable";
import { MaterialThumbnail } from "./MaterialsGrid";
import { mockApiClient } from "../api/client";
import { requestNavigation } from "../navigationGuard";
import { ApiError } from "../api/errors";
import { GalleryStore } from "../api/galleryStore";
import { materialFromDto, internalUserFromDto } from "../api/materialDto";
import { materialArchiveClient, archivePreview, lifecycleEvent } from "../api/materialArchiveClient";
import { archivePreviewDto, lifecycleEventDto } from "../test/materialArchiveFixtures";
import { materialTableClient } from "../api/materialTableClient";
import { previewClient } from "../api/previewClient";
import { projectFromDto, publishedBrandFromDto } from "../api/dto";
import { SessionContext } from "../auth/context";
import { materialDto, materialBrand, materialProject, processorDto } from "../test/materialFixtures";

const first = materialFromDto(materialDto);
const second = { ...first, id: "00000000-0000-4000-8000-000000000023", materialName: "Second material" };
beforeEach(() => {
  localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
function setup(materials = [first, second]) {
  return render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsTable materials={materials} store={new GalleryStore()} client={mockApiClient} projects={[projectFromDto(materialProject)]}
      brands={[publishedBrandFromDto(materialBrand)]} users={[internalUserFromDto(processorDto)]} navigate={vi.fn()} refresh={vi.fn()} onBusyChange={vi.fn()} />
  </SessionContext.Provider>);
}
function bulk() {
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all visible materials" }));
  fireEvent.click(screen.getByRole("button", { name: "Review bulk change" }));
  return screen.getByRole("dialog", { name: "Change 2 materials" });
}
it("edits a cell without opening the detail and uses the row's precise revision", async () => {
  const update = vi.spyOn(materialTableClient, "update").mockResolvedValue({ ...first, isPublished: true, updatedAt: "2026-09-25T12:00:00.123456Z" });
  setup(); fireEvent.click(screen.getByRole("checkbox", { name: `Published for ${first.materialName}` }));
  await screen.findByText("Saved. Refresh materials to reapply the current filters.");
  expect(update).toHaveBeenCalledWith(first, { is_published: true }, expect.any(String));
  expect(screen.getByRole("checkbox", { name: `Published for ${first.materialName}` })).toBeChecked();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("freezes all filtered IDs, shows individual failures, and never retries successful rows", async () => {
  const update = vi.spyOn(materialTableClient, "update").mockResolvedValueOnce({ ...first, workflowStatus: "DONE" }).mockRejectedValueOnce(new ApiError(409, "Changed"));
  setup(); const dialog = bulk();
  expect(update).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Apply change" }));
  await within(dialog).findByText(/1 saved · 1 rejected/);
  expect(update.mock.calls.map(call => call[0].id)).toEqual([first.id, second.id]);
  expect(new Set(update.mock.calls.map(call => call[2])).size).toBe(2);
  expect(within(dialog).getByText(/Reload before retrying/)).toBeVisible();
  expect(within(dialog).queryByRole("button", { name: "Apply change" })).not.toBeInTheDocument();
});
it("pauses on an unknown outcome and retries the exact packet before continuing", async () => {
  const update = vi.spyOn(materialTableClient, "update").mockImplementationOnce(async () => { expect(requestNavigation("/projects")).toBe(false); throw new TypeError("Connection lost"); })
    .mockResolvedValueOnce({ ...first, workflowStatus: "DONE" }).mockResolvedValueOnce({ ...second, workflowStatus: "DONE" });
  setup(); const dialog = bulk(); fireEvent.click(within(dialog).getByRole("button", { name: "Apply change" }));
  const retry = await within(dialog).findByRole("button", { name: "Retry same request and continue" });
  expect(requestNavigation("/projects")).toBe(false);
  expect(update).toHaveBeenCalledTimes(1); expect(within(dialog).getByRole("button", { name: "Close report" })).toBeDisabled();
  fireEvent.click(retry); await within(dialog).findByText(/2 saved · 0 rejected/);
  expect(update.mock.calls[1]).toEqual(update.mock.calls[0]);
  expect(update.mock.calls[2][0].id).toBe(second.id);
  expect(requestNavigation("/projects")).toBe(true);
});
it("does not reinterpret a later 403 as proof that an unknown write was rejected", async () => {
  const update = vi.spyOn(materialTableClient, "update").mockRejectedValueOnce(new TypeError("Lost"))
    .mockRejectedValueOnce(new ApiError(403, "Access changed"));
  setup(); const dialog = bulk(); fireEvent.click(within(dialog).getByRole("button", { name: "Apply change" }));
  fireEvent.click(await within(dialog).findByRole("button", { name: "Retry same request and continue" }));
  await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(within(dialog).getByRole("button", { name: "Retry same request and continue" })).toBeEnabled());
  expect(within(dialog).getByRole("button", { name: "Close report" })).toBeDisabled();
  expect(update.mock.calls[1]).toEqual(update.mock.calls[0]);
});
it("stops after the current request and does not continue after unmount", async () => {
  let finish!: (value: typeof first) => void;
  const update = vi.spyOn(materialTableClient, "update").mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const tree = setup(); const dialog = bulk(); fireEvent.click(within(dialog).getByRole("button", { name: "Apply change" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Stop after current material" }));
  await act(async () => finish(first));
  expect(update).toHaveBeenCalledTimes(1); expect(within(dialog).getByText("Not attempted")).toBeVisible();
  tree.unmount(); expect(update).toHaveBeenCalledTimes(1);
});
it("saves multiline Note explicitly", async () => {
  const update = vi.spyOn(materialTableClient, "update").mockResolvedValue({ ...first, note: "Line one\nLine two" });
  setup([first]); fireEvent.change(screen.getByRole("textbox", { name: `Note for ${first.materialName}` }), { target: { value: "Line one\nLine two" } });
  expect(update).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole("button", { name: "Save note" }));
  await waitFor(() => expect(update).toHaveBeenCalledWith(first, { note: "Line one\nLine two" }, expect.any(String)));
});
it("shows coded category paths and persists only column display preferences", () => {
  setup([first]); expect(screen.getByRole("combobox", { name: `Category for ${first.materialName}` })).toHaveTextContent("K03 · Metal / Tiles");
  fireEvent.click(screen.getByText("Properties"));
  fireEvent.click(screen.getByRole("checkbox", { name: "Folder path" }));
  expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Move .* (left|right)/ })).not.toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Folder path" })).toBeVisible();
  expect(localStorage.getItem("materials.columns.v1")).toContain('"key":"folder","width":340,"visible":true');
  expect(localStorage.getItem("materials.columns.v1")).not.toContain(first.id);
});
it("loads a small primary thumbnail only near the viewport", async () => {
  let observe!: (visible: boolean) => void;
  vi.stubGlobal("IntersectionObserver", class { constructor(callback: (v: { isIntersecting: boolean }[]) => void) { observe = visible => callback([{ isIntersecting: visible }]); } observe() {} disconnect() {} });
  const entries = ["SPHERE_1.png", "FABRIC_1.png"].map((name, i) => ({ name, size: 20, sha256: String(i).repeat(64) }));
  const listing = vi.spyOn(previewClient, "listing").mockResolvedValue({ items: entries, missing: false, ignoredEntries: 0 });
  const image = vi.spyOn(previewClient, "image").mockResolvedValue({ blob: new Blob(["image"]), width: 256, height: 256 });
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:table") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  const row = { ...first, folderPath: `brand/${first.technicalIdentity}` };
  const tree = render(<MaterialThumbnail material={row} store={new GalleryStore()} />);
  expect(listing).not.toHaveBeenCalled(); act(() => observe(true)); await screen.findByRole("img");
  expect(image).toHaveBeenCalledWith(row.id, entries[1], expect.any(AbortSignal), 256);
  tree.unmount(); expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:table");
});

it("preserves a Note draft while saving another cell, and clears a saved Note as null", async () => {
  const saved = { ...first, isPublished: true, updatedAt: "2026-09-25T12:00:01.123456Z" };
  const update = vi.spyOn(materialTableClient, "update").mockResolvedValueOnce(saved).mockResolvedValueOnce({ ...saved, note: "Draft" }).mockResolvedValueOnce({ ...saved, note: null });
  setup([first]);
  const note = () => screen.getByRole("textbox", { name: `Note for ${first.materialName}` });
  fireEvent.change(note(), { target: { value: "Draft" } });
  fireEvent.click(screen.getByRole("checkbox", { name: `Published for ${first.materialName}` }));
  await screen.findByText("Saved. Refresh materials to reapply the current filters.");
  expect(note()).toHaveValue("Draft");
  fireEvent.click(screen.getByRole("button", { name: "Save note" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Save note" })).not.toBeInTheDocument());
  fireEvent.change(note(), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save note" }));
  await waitFor(() => expect(update.mock.calls[2][1]).toEqual({ note: null }));
});
it("stops remaining Done operations when preflight is known unavailable", async () => {
  const update = vi.spyOn(materialTableClient, "update").mockRejectedValue(Object.assign(new ApiError(503, "Unavailable"), { code: "TABLE_PREFLIGHT_UNAVAILABLE" }));
  setup(); const dialog = bulk(); fireEvent.click(within(dialog).getByRole("button", { name: "Apply change" }));
  await within(dialog).findByText("Not attempted");
  expect(update).toHaveBeenCalledTimes(1);
  expect(within(dialog).getByText(/Nothing was changed/)).toBeVisible();
  expect(within(dialog).getByRole("button", { name: "Close report" })).toBeEnabled();
});

it("shows identical editable properties in detail and allows archived database fields", async () => {
  const archived = { ...first, isArchived: true, archivedAt: "2026-09-27T12:00:00Z" };
  const update = vi.spyOn(materialTableClient, "update").mockResolvedValue({ ...first, note: "Archive note" });
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsTable detail materials={[archived]} store={new GalleryStore()} client={mockApiClient} projects={[projectFromDto(materialProject)]}
      brands={[publishedBrandFromDto(materialBrand)]} users={[internalUserFromDto(processorDto)]} navigate={vi.fn()} refresh={vi.fn()} onBusyChange={vi.fn()} />
  </SessionContext.Provider>);
  expect(screen.getByRole("combobox", { name: `Status for ${first.materialName}` })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: `Category for ${first.materialName}` })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: `Order for ${first.materialName}` })).toBeEnabled();
  expect(screen.getByRole("checkbox", { name: `Published for ${first.materialName}` })).toBeEnabled();
  expect(screen.getByRole("checkbox", { name: `Archived for ${first.materialName}` })).toBeChecked();
  const editable = screen.getByLabelText("Editable material properties"), state = screen.getByLabelText("Material state and information");
  expect(within(editable).getAllByRole("combobox")).toHaveLength(6);
  expect(within(editable).getByRole("textbox", { name: `Note for ${first.materialName}` })).toBeVisible();
  expect(within(editable).queryByRole("checkbox")).not.toBeInTheDocument();
  expect(within(state).getAllByRole("checkbox")).toHaveLength(2);
  expect(within(state).queryByRole("combobox")).not.toBeInTheDocument();
  expect(within(editable).getByRole("option", { name: "Correction" })).toHaveAttribute("data-choice-color", "red");
  expect(within(editable).getByRole("option", { name: "Done" })).toHaveAttribute("data-choice-color", "green");
  fireEvent.change(screen.getByRole("textbox", { name: `Note for ${first.materialName}` }), { target: { value: "Archive note" } });
  fireEvent.click(screen.getByRole("button", { name: "Save note" }));
  await waitFor(() => expect(update).toHaveBeenCalledWith(archived, { note: "Archive note" }, expect.any(String)));
  await screen.findByText("Saved. Refresh materials to reapply the current filters.");
  expect(screen.getByRole("checkbox", { name: `Archived for ${first.materialName}` })).toBeChecked();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

function archiveBulk(restoring = false) {
  setup(restoring ? [first, second].map(row => ({ ...row, isArchived: true, archivedAt: "2026-09-27T12:00:00Z" })) : undefined);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all visible materials" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Property" }), { target: { value: "is_archived" } });
  fireEvent.change(screen.getByRole("combobox", { name: "New value" }), { target: { value: restoring ? "false" : "true" } });
  fireEvent.click(screen.getByRole("button", { name: "Review bulk change" }));
  return screen.getByRole("dialog", { name: "Change 2 materials" });
}
it.each([false, true])("changes Archived in bulk with exact recovery (restoring=%s)", async restoring => {
  const version = restoring ? 1 : 0;
  const preview = vi.spyOn(materialArchiveClient, "preview").mockImplementation(async (id, action) => archivePreview(archivePreviewDto(version, id), id, action));
  const command = vi.spyOn(materialArchiveClient, "command").mockRejectedValueOnce(new TypeError("Lost response"))
    .mockImplementation(async current => lifecycleEvent(lifecycleEventDto(version + 1, current.id), current.id));
  const table = vi.spyOn(materialTableClient, "update");
  const dialog = archiveBulk(restoring);
  expect(screen.queryByLabelText("Reason for lifecycle change")).not.toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Apply change" }));
  const retry = await within(dialog).findByRole("button", { name: "Retry same request and continue" });
  expect(requestNavigation("/projects")).toBe(false);
  expect(preview).toHaveBeenCalledTimes(1); expect(command).toHaveBeenCalledTimes(1);
  fireEvent.click(retry);
  await within(dialog).findByText(/2 saved · 0 rejected/);
  expect(command.mock.calls[1]).toEqual(command.mock.calls[0]);
  expect(command.mock.calls[2][0].id).toBe(second.id);
  expect(command.mock.calls[0][2]).toMatchObject({ action: restoring ? "RESTORE" : "ARCHIVE", reason: "Archived property changed." });
  expect(preview).toHaveBeenCalledTimes(2); expect(table).not.toHaveBeenCalled();
});
it("stops bulk archive before writes when eligibility is unavailable", async () => {
  vi.spyOn(materialArchiveClient, "preview").mockRejectedValue(new TypeError("Unavailable"));
  const command = vi.spyOn(materialArchiveClient, "command");
  const dialog = archiveBulk();
  fireEvent.click(within(dialog).getByRole("button", { name: "Apply change" }));
  await within(dialog).findByText("Not attempted");
  expect(within(dialog).getByText(/Nothing was changed/)).toBeVisible();
  expect(within(dialog).getByRole("button", { name: "Close report" })).toBeEnabled();
  expect(command).not.toHaveBeenCalled();
});
it("does not dispatch a bulk archive after unmount during preview", async () => {
  let finish!: (value: ReturnType<typeof archivePreview>) => void;
  vi.spyOn(materialArchiveClient, "preview").mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const command = vi.spyOn(materialArchiveClient, "command");
  const tree = setup();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all visible materials" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Property" }), { target: { value: "is_archived" } });
  fireEvent.click(screen.getByRole("button", { name: "Review bulk change" }));
  fireEvent.click(screen.getByRole("button", { name: "Apply change" }));
  tree.unmount();
  await act(async () => finish(archivePreview(archivePreviewDto(0, first.id), first.id, "ARCHIVE")));
  expect(command).not.toHaveBeenCalled();
});
