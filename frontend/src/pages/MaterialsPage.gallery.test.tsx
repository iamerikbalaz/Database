import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialsPage } from "./MaterialsPage";
import { mockApiClient } from "../api/client";
import { materialFromDto } from "../api/materialDto";
import { materialDto } from "../test/materialFixtures";
import { previewClient } from "../api/previewClient";
import { SessionContext } from "../auth/context";
import { processorDto } from "../test/materialFixtures";
import type { MaterialFilters } from "../api/materialClient";

beforeEach(() => localStorage.clear());
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

it.each([false, true])("filters automatic checks in list and gallery and clears the filter (archived=%s)", async archived => {
  const first = { ...materialFromDto(materialDto), isArchived: archived, automaticFileCheckStatus: "ISSUES" as const };
  const second = { ...first, id: "50000000-0000-4000-8000-000000000002", materialName: "SECOND-MATERIAL", automaticFileCheckStatus: "OK" as const };
  const getMaterials = vi.fn(async (filters: MaterialFilters) => [first, second].filter(row => !filters.automatic_file_check_status || row.automaticFileCheckStatus === filters.automatic_file_check_status));
  render(<MaterialsPage client={{ ...mockApiClient, getMaterials }} navigate={vi.fn()} archived={archived} />);
  await screen.findByRole("table");
  const filter = screen.getByRole("combobox", { name: "Automatic check" });
  expect(Array.from(filter.querySelectorAll("option"), option => [option.value, option.textContent])).toEqual([["", "All"], ["NOT_CHECKED", "Not checked"], ["OK", "OK"], ["ISSUES", "Issues"]]);
  fireEvent.change(filter, { target: { value: "ISSUES" } });
  await waitFor(() => expect(getMaterials).toHaveBeenLastCalledWith({ ...(archived ? { is_archived: "true" } : {}), automatic_file_check_status: "ISSUES" }));
  await screen.findByRole("table");
  expect(screen.queryByRole("link", { name: second.materialName })).not.toBeInTheDocument();
  const calls = getMaterials.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "Gallery" }));
  expect(screen.getByRole("list", { name: "Material gallery" })).toHaveTextContent(first.materialName);
  expect(screen.getByRole("list", { name: "Material gallery" })).not.toHaveTextContent(second.materialName);
  expect(getMaterials).toHaveBeenCalledTimes(calls);
  expect(filter).toHaveValue("ISSUES");
  fireEvent.change(filter, { target: { value: "NOT_CHECKED" } });
  expect(await screen.findByText("No materials found")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  await waitFor(() => expect(getMaterials).toHaveBeenLastCalledWith(archived ? { is_archived: "true" } : {}));
  expect(filter).toHaveValue("");
  expect(await screen.findByRole("list", { name: "Material gallery" })).toHaveTextContent(second.materialName);
});
it("uses the same filtered records in list and four-size gallery, and remembers the display preference", async () => {
  const material = materialFromDto(materialDto);
  const getMaterials = vi.fn().mockResolvedValue([material]);
  const list = vi.spyOn(previewClient, "listing");
  const client = { ...mockApiClient, getMaterials };
  const tree = render(<MaterialsPage client={client} navigate={vi.fn()} />);
  await screen.findByRole("table"); expect(list).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("searchbox", { name: "Search materials" }), { target: { value: "Crystal" } });
  await waitFor(() => expect(getMaterials).toHaveBeenLastCalledWith({ search: "Crystal" })); await screen.findByRole("table");
  const calls = getMaterials.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "Gallery" }));
  expect(screen.queryByRole("table")).not.toBeInTheDocument(); expect(screen.getByRole("list", { name: "Material gallery" })).toHaveTextContent(material.materialName);
  expect(getMaterials).toHaveBeenCalledTimes(calls); expect(screen.getByRole("searchbox")).toHaveValue("Crystal");
  const size = screen.getByRole("combobox", { name: "Preview size" }); expect(size.querySelectorAll("option")).toHaveLength(4);
  fireEvent.change(size, { target: { value: "extra-large" } });
  expect(screen.getByRole("list", { name: "Material gallery" })).toHaveClass("materials-grid--extra-large");
  tree.unmount(); render(<MaterialsPage client={client} navigate={vi.fn()} />);
  await screen.findByRole("list", { name: "Material gallery" }); expect(screen.getByRole("combobox", { name: "Preview size" })).toHaveValue("extra-large");
  fireEvent.click(screen.getByRole("button", { name: "List" })); await screen.findByRole("table");
});

it("uses the same search and property controls in Archive and always requests archived records", async () => {
  const material = { ...materialFromDto(materialDto), isArchived: true, archivedAt: "2026-09-27T12:00:00Z" };
  const getMaterials = vi.fn().mockResolvedValue([material]);
  render(<MaterialsPage client={{ ...mockApiClient, getMaterials }} navigate={vi.fn()} archived />);
  await screen.findByRole("table");
  expect(getMaterials).toHaveBeenLastCalledWith({ is_archived: "true" });
  expect(screen.getByRole("heading", { name: "Archived materials" })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "Archive date" })).toBeVisible();
  expect(screen.getByRole("link", { name: material.materialName })).toHaveAttribute("href", `/material-archives/${material.id}`);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "#archive" } });
  await waitFor(() => expect(getMaterials).toHaveBeenLastCalledWith({ is_archived: "true", search: "#archive" }));
  await screen.findByRole("table"); fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  await waitFor(() => expect(getMaterials).toHaveBeenLastCalledWith({ is_archived: "true" }));
  expect(screen.queryByRole("button", { name: /Select all filtered|Clear selection/ })).not.toBeInTheDocument();
});

it("keeps multi-color filtering and checked selection when switching list/gallery, clearing selection on a filter change", async () => {
  const material = materialFromDto(materialDto), getMaterials = vi.fn().mockResolvedValue([material]);
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsPage client={{ ...mockApiClient, getMaterials }} navigate={vi.fn()} />
  </SessionContext.Provider>);
  await screen.findByRole("table");
  fireEvent.click(screen.getByRole("combobox", { name: "Color" }));
  fireEvent.click(screen.getByRole("option", { name: /White #FFFFFF/ }));
  fireEvent.click(screen.getByRole("option", { name: /Orange #FF822D/ }));
  await waitFor(() => expect(getMaterials).toHaveBeenLastCalledWith({ color_hex: ["#FFFFFF", "#FF822D"] }));
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Color" }), { key: "Escape" });
  await screen.findByRole("table");
  fireEvent.click(screen.getByRole("checkbox", { name: `Select ${material.materialName}` }));
  fireEvent.click(screen.getByRole("button", { name: "Gallery" }));
  expect(screen.getByRole("checkbox", { name: `Select ${material.materialName}` })).toBeChecked();
  expect(screen.getByRole("button", { name: "Prepare selected for publication (1)" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: /Prepare filtered|Publication batches/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "List" }));
  expect(screen.getByRole("checkbox", { name: `Select ${material.materialName}` })).toBeChecked();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "changed" } });
  await screen.findByRole("table");
  expect(screen.getByRole("checkbox", { name: `Select ${material.materialName}` })).not.toBeChecked();
});
