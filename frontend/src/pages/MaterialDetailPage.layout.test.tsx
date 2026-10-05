import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialDetailPage } from "./MaterialDetailPage";
import { SessionContext } from "../auth/context";
import { mockApiClient } from "../api/client";
import { materialFromDto, internalUserFromDto, type Material } from "../api/materialDto";
import { projectFromDto, publishedBrandFromDto } from "../api/dto";
import { catalogClient, contentFromDto } from "../api/catalogClient";
import { metadataClient } from "../api/metadataClient";
import { materialLocalClient, type AutomaticFileCheckResult } from "../api/materialLocalClient";
import { previewClient } from "../api/previewClient";
import { materialDto, materialBrand, materialProject, processorDto } from "../test/materialFixtures";
import { requestNavigation } from "../navigationGuard";

const material = materialFromDto({ ...materialDto, folder_path: "BRAND/LASVIT_9999_G03" });
beforeEach(() => {
  vi.spyOn(catalogClient, "categories").mockResolvedValue([]);
  vi.spyOn(catalogClient, "collections").mockResolvedValue([]);
  vi.spyOn(catalogClient, "content").mockResolvedValue(contentFromDto({ material_id: material.id, revision: 0,
    description: null, credits: null, tags: [], categories: [], collections: [], content_status: "EMPTY" }));
  vi.spyOn(metadataClient, "inspect").mockResolvedValue({ available: false, writesEnabled: false, editable: false,
    expectedUpdatedAt: material.updatedAt, sha256: null, sourceStatus: "MISSING", active: null,
    values: { hex_color: null, width_cm: null, height_cm: null } });
  vi.spyOn(materialLocalClient, "info").mockResolvedValue({ absolutePath: "C:\\Test_data\\BRAND\\LASVIT_9999_G03", canOpen: true, canMove: true });
  vi.spyOn(previewClient, "listing").mockResolvedValue({ items: [], missing: false, ignoredEntries: 0 });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ material_id: material.id, path: "", entries: [], omitted_entries: 0 }))));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function mount(row: Material = material) {
  const client = { ...mockApiClient, getMaterial: vi.fn().mockResolvedValue(row),
    getProjects: vi.fn().mockResolvedValue([projectFromDto(materialProject)]),
    getBrands: vi.fn().mockResolvedValue([publishedBrandFromDto(materialBrand)]),
    getInternalUsers: vi.fn().mockResolvedValue([internalUserFromDto(processorDto)]) };
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialDetailPage id={material.id} client={client} navigate={vi.fn()} />
  </SessionContext.Provider>);
  return client;
}

it("opens a draft card without inventing identity or fetching collections for every customer", async () => {
  const draft: Material = { ...material, isDraft: true, folderPath: null, technicalIdentity: null, sequenceNumber: null,
    publishedBrandId: null, mainCategoryCode: null, assignedProcessorId: null };
  vi.mocked(materialLocalClient.info).mockResolvedValue({ absolutePath: null, canOpen: false, canMove: false });
  mount(draft);
  await screen.findByLabelText("Description");
  expect(screen.getByText("Identity not assigned")).toBeVisible();
  expect(screen.getByRole("button", { name: "Check material data" })).toBeDisabled();
  expect(catalogClient.collections).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Edit Name" })).toBeEnabled();
});

it("renames a name-only draft directly from its material card", async () => {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value() { this.setAttribute("open", ""); } });
  const draft: Material = { ...material, isDraft: true, folderPath: null, technicalIdentity: null, sequenceNumber: null,
    publishedBrandId: null, mainCategoryCode: null, assignedProcessorId: null };
  const client = mount(draft);
  const updated = { ...draft, materialName: "RENAMED-DRAFT" };
  const update = vi.spyOn(client, "updateMaterial").mockResolvedValue(updated);
  await screen.findByLabelText("Description");
  client.getMaterial.mockResolvedValue(updated);
  fireEvent.click(screen.getByRole("button", { name: "Edit Name" }));
  fireEvent.change(screen.getByLabelText("Material name"), { target: { value: "renamed draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save name" }));
  expect(await screen.findByRole("heading", { name: "RENAMED-DRAFT" })).toBeVisible();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(update).toHaveBeenCalledExactlyOnceWith(draft.id, { material_name: "RENAMED DRAFT" }, expect.any(String));
});

it("keeps the header check action mounted, blocks edits while checking and retains its report after material refresh", async () => {
  let complete!: (value: AutomaticFileCheckResult) => void;
  vi.spyOn(materialLocalClient, "check").mockImplementation(() => new Promise(resolve => { complete = resolve; }));
  const client = mount();
  const name = await screen.findByRole("heading", { name: material.materialName });
  await screen.findByLabelText("Description");
  await waitFor(() => expect(screen.getByRole("button", { name: "Open folder" })).toBeEnabled());
  const check = screen.getByRole("button", { name: "Check material data" });
  expect(name.closest(".page-heading")).toContainElement(check);
  expect(name.closest(".material-name-heading")).toContainElement(screen.getByRole("button", { name: "Edit Name" }));
  expect(screen.queryByRole("heading", { name: "Automatic file check" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Preview gallery" })).not.toBeInTheDocument();
  fireEvent.click(check);
  expect(screen.getByRole("progressbar", { name: "Automatic file check progress" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Checking material data…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Edit Name" })).toBeDisabled();
  expect(screen.getByLabelText(`Status for ${material.materialName}`)).toBeDisabled();
  expect(screen.getByLabelText("Description")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Open folder" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reload source" })).toBeDisabled();
  expect(requestNavigation("/customers")).toBe(false);
  const updatedAt = "2026-10-01T16:00:00Z";
  client.getMaterial.mockResolvedValue({ ...material, updatedAt, automaticFileCheckStatus: "OK" });
  await act(async () => complete({ materialId: material.id, status: "OK", checkedAt: updatedAt, updatedAt,
    profile: "PBR_FILES_V1", complete: true, issues: [], report: "Stable completed report" }));
  await screen.findByLabelText("Description");
  expect(screen.getByRole("textbox", { name: "Issues and report" })).toHaveValue("Issues\nNo issues found by the current checks.\n\nStable completed report");
  expect(screen.getByRole("button", { name: "Check material data" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Edit Name" })).toBeEnabled();
  expect(requestNavigation("/customers")).toBe(true);
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Unsaved draft" } });
  expect(screen.getByRole("button", { name: "Check material data" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reload source" })).toBeDisabled();
});

it("places icon refresh actions in the folder and library headings and refreshes folder data on demand", async () => {
  mount();
  await screen.findByLabelText("Description");
  await screen.findByText("This folder is empty.");
  const folderHeading = screen.getByRole("heading", { name: "Material data folder" }).closest(".material-section-heading");
  const folderRefresh = screen.getByRole("button", { name: "Refresh contents" });
  expect(folderHeading).toContainElement(folderRefresh);
  expect(folderRefresh.textContent).toBe("");
  expect(folderRefresh.querySelector("svg")).not.toBeNull();
  const sourceRefresh = screen.getByRole("button", { name: "Reload source" });
  expect(screen.getByRole("heading", { name: "Material data for library" }).closest(".material-section-heading")).toContainElement(sourceRefresh);
  expect(sourceRefresh.textContent).toBe("");
  fireEvent.click(folderRefresh);
  await waitFor(() => expect(materialLocalClient.info).toHaveBeenCalledTimes(2));
  await screen.findByText("This folder is empty.");
  expect(vi.mocked(fetch).mock.calls).toHaveLength(2);
});
