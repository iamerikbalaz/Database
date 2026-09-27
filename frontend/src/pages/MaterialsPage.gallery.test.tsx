import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialsPage } from "./MaterialsPage";
import { mockApiClient } from "../api/client";
import { materialFromDto } from "../api/materialDto";
import { materialDto } from "../test/materialFixtures";
import { previewClient } from "../api/previewClient";

beforeEach(() => localStorage.clear());
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });
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
