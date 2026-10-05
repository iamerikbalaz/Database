import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialsPage } from "./MaterialsPage";
import { mockApiClient } from "../api/client";
import { materialFromDto } from "../api/materialDto";
import { materialTableClient } from "../api/materialTableClient";
import { materialLocalClient } from "../api/materialLocalClient";
import { identityClient } from "../api/identityClient";
import { catalogClient } from "../api/catalogClient";
import { SessionContext } from "../auth/context";
import { requestNavigation } from "../navigationGuard";
import { materialDto, processorDto } from "../test/materialFixtures";

beforeEach(() => { localStorage.clear(); vi.spyOn(catalogClient, "categories").mockResolvedValue([]); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });

function viewport() {
  const handlers = new Set<() => void>();
  const media = { matches: true, addEventListener: vi.fn((_event: string, callback: () => void) => handlers.add(callback)), removeEventListener: vi.fn((_event: string, callback: () => void) => handlers.delete(callback)) };
  vi.stubGlobal("matchMedia", vi.fn(() => media));
  return { media, resize: (matches: boolean) => act(() => { media.matches = matches; handlers.forEach(callback => callback()); }) };
}

it.each([false, true])("retains selected rows, highlighted rows and note drafts when responsive mode changes (archived=%s)", async archived => {
  const { media, resize } = viewport();
  const material = { ...materialFromDto(materialDto), isArchived: archived };
  const getMaterials = vi.fn().mockResolvedValue([material]);
  const tree = render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsPage client={{ ...mockApiClient, getMaterials }} navigate={vi.fn()} archived={archived} />
  </SessionContext.Provider>);
  const table = await screen.findByRole("table");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "#review" } });
  await waitFor(() => expect(getMaterials).toHaveBeenLastCalledWith({ search: "#review", ...(archived ? { is_archived: "true" } : {}) }));
  await screen.findByRole("table");
  const draft = screen.getByRole("textbox", { name: `Note for ${material.materialName}` });
  fireEvent.change(draft, { target: { value: "Unfinished note" } });
  fireEvent.click(screen.getByRole("checkbox", { name: `Select ${material.materialName}` }));
  fireEvent.click(screen.getByRole("row", { name: `Material row ${material.materialName}` }));
  const currentTable = screen.getByRole("table");
  const calls = getMaterials.mock.calls.length;
  expect(currentTable.closest(".database-table-viewport")).toHaveClass("database-table-viewport--contained");
  resize(false);
  expect(screen.getByRole("table")).toBe(currentTable);
  expect(screen.getByRole("textbox", { name: `Note for ${material.materialName}` })).toBe(draft);
  expect(draft).toHaveValue("Unfinished note");
  expect(screen.getByRole("checkbox", { name: `Select ${material.materialName}` })).toBeChecked();
  expect(screen.getByRole("row", { name: `Material row ${material.materialName}` })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("searchbox")).toHaveValue("#review");
  expect(currentTable.closest(".database-table-viewport")).not.toHaveClass("database-table-viewport--contained");
  resize(true);
  expect(screen.getByRole("table")).toBe(currentTable);
  expect(draft).toHaveValue("Unfinished note");
  expect(currentTable.closest(".database-table-viewport")).toHaveClass("database-table-viewport--contained");
  expect(getMaterials).toHaveBeenCalledTimes(calls);
  expect(table).not.toBe(currentTable); // Filtering reloads data; resizing must not.
  tree.unmount();
  expect(media.removeEventListener).toHaveBeenCalledWith("change", expect.any(Function));
});

it("keeps the same pending material write and navigation guard while resizing", async () => {
  const { resize } = viewport();
  const material = materialFromDto(materialDto);
  let finish!: (value: typeof material) => void;
  const update = vi.spyOn(materialTableClient, "update").mockReturnValue(new Promise(resolve => { finish = resolve; }));
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsPage client={{ ...mockApiClient, getMaterials: vi.fn().mockResolvedValue([material]) }} navigate={vi.fn()} />
  </SessionContext.Provider>);
  await screen.findByRole("table");
  fireEvent.click(screen.getByRole("checkbox", { name: `Published for ${material.materialName}` }));
  resize(false); resize(true);
  expect(update).toHaveBeenCalledOnce();
  expect(screen.getByRole("searchbox")).toBeDisabled();
  expect(requestNavigation("/orders")).toBe(false);
  await act(async () => finish({ ...material, isPublished: !material.isPublished, updatedAt: "2026-09-30T14:00:00Z" }));
  expect(screen.getByRole("searchbox")).toBeEnabled();
  expect(requestNavigation("/orders")).toBe(true);
});

it("retains the gallery, size and checked selection across responsive changes", async () => {
  const { resize } = viewport();
  const material = materialFromDto(materialDto), getMaterials = vi.fn().mockResolvedValue([material]);
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsPage client={{ ...mockApiClient, getMaterials }} navigate={vi.fn()} initialView="gallery" />
  </SessionContext.Provider>);
  const gallery = await screen.findByRole("list", { name: "Material gallery" });
  fireEvent.change(screen.getByRole("combobox", { name: "Preview size" }), { target: { value: "large" } });
  fireEvent.click(screen.getByRole("checkbox", { name: `Select ${material.materialName}` }));
  resize(false); resize(true);
  expect(screen.getByRole("list", { name: "Material gallery" })).toBe(gallery);
  expect(gallery).toHaveClass("materials-grid--large");
  expect(screen.getByRole("checkbox", { name: `Select ${material.materialName}` })).toBeChecked();
  expect(getMaterials).toHaveBeenCalledOnce();
});

it.each(["list", "gallery"] as const)("groups selected actions in the bulk panel and retains check report after %s reload", async view => {
  viewport();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  const material = materialFromDto(materialDto), getMaterials = vi.fn().mockResolvedValue([material]);
  const report = "Checked: 1. OK: 1. Issues: 0.";
  const check = vi.spyOn(materialLocalClient, "checkMany").mockResolvedValue({ items: [], report, reportPath: null, reportOpened: false });
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsPage client={{ ...mockApiClient, getMaterials }} navigate={vi.fn()} initialView={view === "gallery" ? "gallery" : undefined} />
  </SessionContext.Provider>);
  fireEvent.click(await screen.findByRole("checkbox", { name: `Select ${material.materialName}` }));
  const start = screen.getByRole("button", { name: "Auto-check materials (1)" });
  const publish = screen.getByRole("button", { name: "Prepare for publication (1)" });
  const refresh = screen.getByRole("button", { name: view === "gallery" ? "Refresh previews" : "Refresh materials" });
  const bulk = screen.getByRole("group", { name: "Apply to 1 selected materials" });
  expect(bulk).toContainElement(start); expect(bulk).toContainElement(publish);
  expect(bulk).not.toContainElement(refresh);
  expect(start.compareDocumentPosition(publish) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(start.closest(".material-bulk-actions")).toBe(publish.parentElement);
  fireEvent.click(start);
  await waitFor(() => expect(getMaterials).toHaveBeenCalledTimes(2));
  fireEvent.click(await screen.findByRole("button", { name: "View check report" }));
  expect(screen.getByRole("textbox", { name: "Automatic file check report" })).toHaveValue(report);
  expect(check).toHaveBeenCalledOnce();
});

it.each(["list", "gallery"] as const)("opens bulk name editing for the explicit %s selection and protects that selection", async view => {
  viewport();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  const material = materialFromDto(materialDto);
  const history = vi.spyOn(identityClient, "operations").mockResolvedValue({ enabled: true, items: [] });
  const confirm = vi.spyOn(identityClient, "confirm");
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsPage client={{ ...mockApiClient, getMaterials: vi.fn().mockResolvedValue([material]) }} navigate={vi.fn()} initialView={view === "gallery" ? "gallery" : undefined} />
  </SessionContext.Provider>);
  const selected = await screen.findByRole("checkbox", { name: `Select ${material.materialName}` });
  expect(screen.queryByRole("button", { name: "Edit names" })).not.toBeInTheDocument();
  fireEvent.click(selected);
  const button = screen.getByRole("button", { name: "Edit names" });
  expect(screen.getByRole("group", { name: "Apply to 1 selected materials" })).toContainElement(button);
  fireEvent.click(button);
  expect(await screen.findByRole("dialog", { name: "Edit material names" })).toBeVisible();
  await waitFor(() => expect(history).toHaveBeenCalledWith(material.id));
  expect(screen.getByRole("searchbox")).toBeDisabled();
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog", { name: "Edit material names" })).not.toBeInTheDocument();
  expect(screen.getByRole("searchbox")).toBeEnabled();
  expect(screen.getByRole("checkbox", { name: `Select ${material.materialName}` })).toBeChecked();
});

it("sorts without reloading or losing note drafts and keeps selected filters for this user", async () => {
  viewport();
  const material = materialFromDto(materialDto);
  const records = [{ ...material, materialName: "WOOD", sequenceNumber: 10, createdAt: "2026-09-01T00:00:00Z" }, { ...material, id: "00000000-0000-4000-8000-000000000009", materialName: "STONE", sequenceNumber: 2, createdAt: "2026-10-01T00:00:00Z" }];
  const getMaterials = vi.fn().mockResolvedValue(records);
  const content = <SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsPage client={{ ...mockApiClient, getMaterials }} navigate={vi.fn()} />
  </SessionContext.Provider>;
  const view = render(content); await screen.findByRole("table");
  const sort = screen.getByRole("combobox", { name: "Sort materials" });
  expect(sort).toHaveValue("created-desc"); expect(sort.closest("fieldset")).toBeNull();
  expect(sort.closest(".database-results-toolbar")).toContainElement(screen.getByRole("group", { name: "Material display" }));
  const draft = screen.getByRole("textbox", { name: "Note for WOOD" });
  fireEvent.change(draft, { target: { value: "Draft" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Select WOOD" }));
  const names = () => screen.getAllByRole("row").filter(row => row.hasAttribute("aria-label")).map(row => row.getAttribute("aria-label"));
  expect(names()).toEqual(["Material row STONE", "Material row WOOD"]);
  fireEvent.change(sort, { target: { value: "number-desc" } });
  expect(names()).toEqual(["Material row WOOD", "Material row STONE"]);
  fireEvent.change(sort, { target: { value: "number-asc" } });
  expect(names()).toEqual(["Material row STONE", "Material row WOOD"]);
  fireEvent.change(screen.getByRole("combobox", { name: "Sort materials" }), { target: { value: "created-asc" } });
  expect(names()).toEqual(["Material row WOOD", "Material row STONE"]);
  expect(screen.getByRole("textbox", { name: "Note for WOOD" })).toBe(draft);
  expect(draft).toHaveValue("Draft"); expect(screen.getByRole("checkbox", { name: "Select WOOD" })).toBeChecked();
  expect(getMaterials).toHaveBeenCalledOnce();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "#keep" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Keep filters" }));
  await screen.findByRole("table"); view.unmount(); render(content);
  await waitFor(() => expect(getMaterials).toHaveBeenLastCalledWith({ search: "#keep" }));
  expect(screen.getByRole("searchbox")).toHaveValue("#keep");
  expect(screen.getByRole("combobox", { name: "Sort materials" })).toHaveValue("created-asc");
});

it("offers updated catalog codes for future identity changes and retains historical categories", async () => {
  viewport();
  const material = materialFromDto(materialDto);
  vi.mocked(catalogClient.categories).mockResolvedValue([{ id: "00000000-0000-4000-8000-000000000001", value: "New stone category", abbreviation: "NEW01", aliases: [material.mainCategoryCode, "NEW01"], version: 2, active: true, brandId: null, createdAt: "2026-10-01T00:00:00Z" }]);
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsPage client={{ ...mockApiClient, getMaterials: vi.fn().mockResolvedValue([material]) }} navigate={vi.fn()} />
  </SessionContext.Provider>);
  await screen.findByRole("table");
  const rowCategory = screen.getByRole("combobox", { name: `Category for ${material.materialName}` });
  expect(rowCategory).toHaveValue(material.mainCategoryCode);
  expect(rowCategory.querySelector(`option[value="${material.mainCategoryCode}"]`)).toHaveTextContent("New stone category");
  expect(rowCategory.querySelector('option[value="NEW01"]')).toHaveTextContent("New stone category");
  expect(screen.getByRole("combobox", { name: "Main category" }).querySelector('option[value="NEW01"]')).toHaveTextContent("New stone category");
});
