import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CustomersPage } from "./CustomersPage";
import { directoryClient, parseCustomer } from "../api/directoryClient";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";
import { processorDto } from "../test/materialFixtures";
import { customerDtos } from "../test/directoryFixtures";
import { catalogClient } from "../api/catalogClient";

const customers = customerDtos.map((row, index) => parseCustomer({ ...row, is_published: index === 0 }));
function page(userId = processorDto.id, role: Role = "ADMIN") {
  return <SessionContext.Provider value={{ session: { user: { ...processorDto, id: userId, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}><CustomersPage navigate={vi.fn()} /></SessionContext.Provider>;
}
beforeEach(() => {
  localStorage.clear();
  vi.spyOn(directoryClient, "customers").mockResolvedValue(customers);
  vi.spyOn(catalogClient, "categories").mockResolvedValue([]);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:synthetic-export"), revokeObjectURL: vi.fn() }));
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });

it("exports exactly the selected customers without marking them published or changing records", async () => {
  const exportCsv = vi.spyOn(directoryClient, "exportCustomers").mockResolvedValue({ filename: "CSV_BRANDS.csv", csv: "\ufeffbrand_identifier;name\r\nF001;Synthetic\r\n", warnings: [] });
  const save = vi.spyOn(directoryClient, "saveCustomer");
  const downloads: { filename: string; href: string }[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { downloads.push({ filename: this.download, href: this.href }); });
  render(page()); await screen.findByRole("link", { name: customers[0].name });
  expect(screen.getByRole("button", { name: "Export selected brands CSV (0)" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: `Select ${customers[1].name}` }));
  fireEvent.click(screen.getByRole("button", { name: "Export selected brands CSV (1)" }));
  await screen.findByText("CSV downloaded. Published remains unchanged.");
  expect(exportCsv).toHaveBeenCalledExactlyOnceWith([customers[1].id]);
  expect(downloads).toEqual([{ filename: "CSV_BRANDS.csv", href: "blob:synthetic-export" }]);
  expect(save).not.toHaveBeenCalled();
  expect(screen.getByRole("checkbox", { name: `Published for ${customers[1].name}` })).not.toBeChecked();
});

it("filters publication state and sends an inline change with the expected customer revision", async () => {
  const save = vi.spyOn(directoryClient, "saveCustomer").mockResolvedValue({ ...customers[1], isPublished: true, updatedAt: "2026-10-01T10:00:00Z" });
  render(page()); await screen.findByRole("link", { name: customers[0].name });
  fireEvent.change(screen.getByRole("combobox", { name: "Published" }), { target: { value: "false" } });
  expect(screen.queryByRole("link", { name: customers[0].name })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: `Published for ${customers[1].name}` }));
  await screen.findByText("Saved. Refresh to reapply filters.");
  expect(save).toHaveBeenCalledExactlyOnceWith(customers[1].id, { is_published: true, expected_updated_at: customers[1].updatedAt }, expect.any(String));
});

it("restores opted-in Customer filters and sort for their user and clears values without disabling persistence", async () => {
  const view = render(page()); await screen.findByRole("link", { name: customers[0].name });
  const sort = screen.getByRole("combobox", { name: "Sort by" });
  expect(screen.getByRole("checkbox", { name: "Keep filters" }).closest(".database-heading-actions")).toContainElement(screen.getByRole("link", { name: "Add customer" }));
  expect(sort).toHaveValue("name-asc"); expect(sort.closest("fieldset")).toBeNull();
  fireEvent.change(screen.getByRole("combobox", { name: "Published" }), { target: { value: "true" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Sort by" }), { target: { value: "created-desc" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Keep filters" }));
  view.unmount(); const restored = render(page()); await screen.findByRole("link", { name: customers[0].name });
  expect(screen.getByRole("combobox", { name: "Published" })).toHaveValue("true");
  expect(screen.getByRole("combobox", { name: "Sort by" })).toHaveValue("created-desc");
  expect(screen.queryByRole("link", { name: customers[1].name })).not.toBeInTheDocument();
  restored.rerender(page("00000000-0000-4000-8000-000000000099"));
  expect(screen.getByRole("combobox", { name: "Published" })).toHaveValue("");
  expect(screen.getByRole("checkbox", { name: "Keep filters" })).not.toBeChecked();
  restored.rerender(page());
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(screen.getByRole("combobox", { name: "Published" })).toHaveValue("");
  expect(screen.getByRole("checkbox", { name: "Keep filters" })).toBeChecked();
});

it("prevents duplicate export clicks and keeps server validation errors visible", async () => {
  let fail!: (error: Error) => void;
  const exportCsv = vi.spyOn(directoryClient, "exportCustomers").mockReturnValue(new Promise((_resolve, reject) => { fail = reject; }));
  render(page()); fireEvent.click(await screen.findByRole("checkbox", { name: `Select ${customers[0].name}` }));
  const button = screen.getByRole("button", { name: "Export selected brands CSV (1)" });
  fireEvent.click(button); fireEvent.click(button);
  expect(exportCsv).toHaveBeenCalledOnce(); expect(button).toBeDisabled();
  await act(async () => fail(new Error("Provide one valid Brand identifier.")));
  await waitFor(() => expect(button).toBeEnabled());
  expect(screen.getByRole("status")).toHaveTextContent("Provide one valid Brand identifier.");
});

it("does not offer CSV export or publication editing to a read-only directory role", async () => {
  render(page(processorDto.id, "PROCESSOR")); await screen.findByRole("link", { name: customers[0].name });
  expect(screen.queryByRole("button", { name: /Export selected brands CSV/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox", { name: `Published for ${customers[0].name}` })).not.toBeInTheDocument();
});

it("shows renamed category labels and filters historical customer material codes by the current code", async () => {
  vi.mocked(catalogClient.categories).mockResolvedValue([{ id: "00000000-0000-4000-8000-000000000001", value: "Corrected glass", abbreviation: "GLASS", aliases: ["H01", "GLASS"], active: true, version: 2, brandId: null, createdAt: "2026-10-01T00:00:00Z" }]);
  render(page()); await screen.findByRole("link", { name: customers[0].name });
  fireEvent.change(screen.getByRole("combobox", { name: "Main category" }), { target: { value: "GLASS" } });
  expect(screen.getByRole("link", { name: customers[0].name })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: customers[1].name })).not.toBeInTheDocument();
  expect(screen.getByText("H01 · Corrected glass")).toBeInTheDocument();
});
