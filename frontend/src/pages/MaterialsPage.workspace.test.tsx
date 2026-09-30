import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialsPage } from "./MaterialsPage";
import { mockApiClient } from "../api/client";
import { materialFromDto } from "../api/materialDto";
import { materialTableClient } from "../api/materialTableClient";
import { SessionContext } from "../auth/context";
import { requestNavigation } from "../navigationGuard";
import { materialDto, processorDto } from "../test/materialFixtures";

beforeEach(() => localStorage.clear());
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
