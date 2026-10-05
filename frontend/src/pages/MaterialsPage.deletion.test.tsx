import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialsPage } from "./MaterialsPage";
import { mockApiClient } from "../api/client";
import { materialFromDto } from "../api/materialDto";
import { materialDeletionClient } from "../api/materialDeletionClient";
import { materialDto, processorDto } from "../test/materialFixtures";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";

const first = materialFromDto(materialDto);
const second = { ...first, id: "50000000-0000-4000-8000-000000000002", materialName: "SECOND" };
beforeEach(() => {
  localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  vi.spyOn(materialDeletionClient, "pending").mockResolvedValue([]);
  vi.spyOn(materialDeletionClient, "plan").mockResolvedValue({ proposal_hash: "a".repeat(64), can_apply: true, mode: "RECORD_ONLY", total: 1, warnings: [],
    items: [{ material_id: first.id, material_name: first.materialName, identity: first.technicalIdentity, folder_path: first.folderPath, file_count: null, issues: [] }] });
  vi.spyOn(materialDeletionClient, "apply").mockResolvedValue({ id: "50000000-0000-4000-8000-000000000099", mode: "RECORD_ONLY", status: "COMPLETED", deleted_count: 1, quarantine_retained: false,
    items: [{ material_id: first.id, material_name: first.materialName, identity: first.technicalIdentity, status: "COMPLETED", error_code: null }] });
});
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });
function setup(role: Role, archived = false, gallery = false) {
  const getMaterials = vi.fn().mockResolvedValue([first, second].map(item => ({ ...item, isArchived: archived })));
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsPage client={{ ...mockApiClient, getMaterials }} navigate={vi.fn()} archived={archived} initialView={gallery ? "gallery" : undefined} />
  </SessionContext.Provider>);
  return { getMaterials };
}

it.each([[false, false], [false, true], [true, false], [true, true]])("offers admin deletion only after selection (archived=%s gallery=%s)", async (archived, gallery) => {
  setup("ADMIN", archived, gallery);
  await screen.findByRole(gallery ? "list" : "table", gallery ? { name: "Material gallery" } : {});
  expect(screen.queryByRole("button", { name: "Delete selected materials" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: `Select ${first.materialName}` }));
  fireEvent.click(screen.getByRole("button", { name: "Delete selected materials" }));
  const dialog = await screen.findByRole("dialog", { name: "Delete selected materials" });
  expect(within(dialog).getByText(/1 explicitly selected material/)).toBeInTheDocument();
  expect(materialDeletionClient.pending).toHaveBeenCalledWith([first.id]);
});

it.each<Role>(["PRODUCTION_LEAD", "PROCESSOR", "LEADERSHIP"])("does not offer deletion to %s even with row selection", async role => {
  setup(role); await screen.findByRole("table");
  const checkbox = screen.queryByRole("checkbox", { name: `Select ${first.materialName}` });
  if (checkbox) fireEvent.click(checkbox);
  expect(screen.queryByRole("button", { name: "Delete selected materials" })).not.toBeInTheDocument();
  expect(materialDeletionClient.pending).not.toHaveBeenCalled();
});

it("refreshes rows and clears selection only after the completed report is closed", async () => {
  const { getMaterials } = setup("ADMIN");
  fireEvent.click(await screen.findByRole("checkbox", { name: `Select ${first.materialName}` }));
  fireEvent.click(screen.getByRole("button", { name: "Delete selected materials" }));
  const dialog = screen.getByRole("dialog", { name: "Delete selected materials" });
  await waitFor(() => expect(within(dialog).getByRole("button", { name: "Review deletion" })).toBeEnabled());
  fireEvent.click(within(dialog).getByRole("button", { name: "Review deletion" }));
  fireEvent.click(await within(dialog).findByRole("checkbox", { name: /I reviewed these materials/ }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Confirm delete" }));
  await within(dialog).findByText("1 deleted · COMPLETED");
  expect(getMaterials).toHaveBeenCalledOnce();
  getMaterials.mockResolvedValue([second]);
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(getMaterials).toHaveBeenCalledTimes(2));
  await screen.findByRole("table");
  expect(screen.getByText("0 selected")).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox", { name: `Select ${first.materialName}` })).not.toBeInTheDocument();
});
