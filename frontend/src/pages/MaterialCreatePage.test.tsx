import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialCreatePage } from "./MaterialCreatePage";
import { pastedMaterialNames } from "../forms/materialNames";
import { mockApiClient } from "../api/client";
import { catalogClient, type CatalogValue } from "../api/catalogClient";
import { directoryClient, type Customer, type Order } from "../api/directoryClient";
import { materialCreationClient, type MaterialCreationResult } from "../api/materialCreationClient";
import { internalUserFromDto } from "../api/materialDto";
import { materialDto, processorDto } from "../test/materialFixtures";
import { requestNavigation } from "../navigationGuard";

const customer = { id: materialDto.published_brand_id, name: "Customer", isActive: true } as Customer;
const order = { id: materialDto.project_id, customerId: customer.id, generatedName: "Order 0246" } as Order;
const category = { id: "70000000-0000-4000-8000-000000000001", value: "Fabrics", abbreviation: "F", active: true, version: 1, brandId: null, createdAt: null } satisfies CatalogValue;
const extra = { ...category, id: "70000000-0000-4000-8000-000000000002", value: "Upholstery", abbreviation: "F03" };
const collection = { ...extra, id: "70000000-0000-4000-8000-000000000003", value: "Collection", brandId: customer.id };
const result: MaterialCreationResult = { id: "60000000-0000-4000-8000-000000000001", status: "COMPLETED", completedCount: 1, totalCount: 1,
  items: [{ materialId: materialDto.id, name: "NEW-MATERIAL", identity: "CUSTOMER_0001_NEW-MATERIAL_F", folderPath: "CUSTOMER/CUSTOMER_0001_NEW-MATERIAL_F", status: "COMPLETED", errorCode: null }] };
beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(directoryClient, "customers").mockResolvedValue([customer]); vi.spyOn(directoryClient, "orders").mockResolvedValue([order]);
  vi.spyOn(catalogClient, "categories").mockResolvedValue([category, extra]); vi.spyOn(catalogClient, "collections").mockResolvedValue([collection]);
  vi.spyOn(materialCreationClient, "options").mockResolvedValue({ pathsVersion: 2, templates: [{ name: "base.sbs", sizeBytes: 10 }] });
  vi.spyOn(materialCreationClient, "create").mockResolvedValue(result);
});
afterEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });
function setup() {
  return render(<MaterialCreatePage client={{ ...mockApiClient, getInternalUsers: vi.fn().mockResolvedValue([internalUserFromDto(processorDto)]) }} navigate={vi.fn()} />);
}
async function fill() {
  await screen.findByRole("form", { name: "Add material" });
  for (const [label, value] of [["Customer", customer.id], ["Processor", processorDto.id], ["Main category", "F"], ["SBS template", "base.sbs"], ["Material name", "New material"]])
    fireEvent.change(screen.getByLabelText(label, { exact: true }), { target: { value } });
}
it("creates without Order, includes additional categories and Customer collections", async () => {
  setup(); await fill();
  fireEvent.click(screen.getByText(/Additional categories/));
  fireEvent.click(screen.getByText(/Brand collections/));
  fireEvent.click(screen.getByRole("checkbox", { name: "F03 · Upholstery" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Collection" }));
  expect(screen.getByRole("checkbox", { name: "F · Fabrics (Main category)" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Create material" }));
  await screen.findByRole("heading", { name: "1 of 1 materials created" });
  expect(materialCreationClient.create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ project_id: null, published_brand_id: customer.id,
    names: ["New material"], template_name: "base.sbs", expected_paths_version: 2, category_ids: [extra.id], collection_ids: [collection.id] }));
  expect(vi.mocked(materialCreationClient.create).mock.calls[0][0]).not.toHaveProperty("resolution");
});
it("selecting Order sets and locks Customer until Order is cleared", async () => {
  setup(); await screen.findByLabelText("Order");
  fireEvent.change(screen.getByLabelText("Order"), { target: { value: order.id } });
  expect(screen.getByLabelText("Customer")).toHaveValue(customer.id); expect(screen.getByLabelText("Customer")).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Order"), { target: { value: "" } });
  expect(screen.getByLabelText("Customer")).toBeEnabled();
});
it("pastes one Excel column into an explicit multiple-material creation", async () => {
  setup(); await fill(); fireEvent.click(screen.getByRole("checkbox", { name: "Create multiple materials" }));
  fireEvent.change(screen.getByRole("textbox", { name: /one Excel column/ }), { target: { value: "Orange tiles\r\nYellow tiles\r\n" } });
  fireEvent.click(screen.getByRole("button", { name: "Create materials" }));
  await waitFor(() => expect(materialCreationClient.create).toHaveBeenCalledWith(expect.objectContaining({ names: ["Orange tiles", "Yellow tiles"] })));
});
it("rejects several pasted Excel columns without creating records", async () => {
  setup(); await fill(); fireEvent.click(screen.getByRole("checkbox", { name: "Create multiple materials" }));
  fireEvent.change(screen.getByRole("textbox", { name: /one Excel column/ }), { target: { value: "Orange\tTiles\nYellow\tTiles" } });
  fireEvent.click(screen.getByRole("button", { name: "Create materials" }));
  expect(screen.getByRole("alert")).toHaveTextContent("one Excel column only"); expect(materialCreationClient.create).not.toHaveBeenCalled();
});
it("recovers an older frozen resolution request without showing or changing its fields", async () => {
  const original = { idempotency_key: "60000000-0000-4000-8000-000000000010", expected_paths_version: 2, project_id: null,
    published_brand_id: customer.id, assigned_processor_id: processorDto.id, main_category_code: "F", names: ["New material"],
    category_ids: [], collection_ids: [], resolution: 12, template_name: "base.sbs" };
  sessionStorage.setItem("reawote.material-create.current", JSON.stringify(original));
  setup();
  const recover = await screen.findByRole("button", { name: "Recover / finish this batch" });
  expect(screen.queryByLabelText("Resolution (K)")).not.toBeInTheDocument();
  fireEvent.click(recover);
  await screen.findByRole("heading", { name: "1 of 1 materials created" });
  expect(materialCreationClient.create).toHaveBeenCalledExactlyOnceWith(original);
});

it("collapses catalog choices without losing selected values or the names draft", async () => {
  setup(); await fill();
  const summary = screen.getByText(/Additional categories/);
  expect(summary.closest("details")).not.toHaveAttribute("open");
  fireEvent.click(summary);
  fireEvent.click(screen.getByRole("checkbox", { name: "F03 · Upholstery" }));
  fireEvent.click(summary); fireEvent.click(summary);
  expect(screen.getByRole("checkbox", { name: "F03 · Upholstery" })).toBeChecked();
  expect(screen.getByRole("textbox", { name: "Material name" })).toHaveValue("New material");
});
it("recovers a lost response with the identical persisted request and blocks duplicate submits", async () => {
  vi.mocked(materialCreationClient.create).mockRejectedValueOnce(new TypeError("Lost response"));
  const view = setup(); await fill(); fireEvent.click(screen.getByRole("button", { name: "Create material" }));
  await screen.findByText(/result is unknown/);
  const first = vi.mocked(materialCreationClient.create).mock.calls[0][0];
  expect(requestNavigation("/materials")).toBe(false);
  view.unmount(); setup();
  const recover = await screen.findByRole("button", { name: "Recover / finish this batch" });
  fireEvent.click(recover); fireEvent.click(recover);
  await screen.findByRole("heading", { name: "1 of 1 materials created" });
  expect(materialCreationClient.create).toHaveBeenCalledTimes(2);
  expect(vi.mocked(materialCreationClient.create).mock.calls[1][0]).toEqual(first);
});
it("shows per-item partial outcome and resumes the same batch without editing common fields", async () => {
  vi.mocked(materialCreationClient.create).mockResolvedValueOnce({ ...result, status: "PARTIAL", completedCount: 0, items: [{ ...result.items[0], status: "FAILED", errorCode: "MATERIAL_FOLDER_EXISTS" }] });
  setup(); await fill(); fireEvent.click(screen.getByRole("button", { name: "Create material" }));
  await screen.findByText(/FAILED: MATERIAL_FOLDER_EXISTS/);
  expect(screen.getByLabelText("Material name")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Recover / finish this batch" }));
  await screen.findByRole("heading", { name: "1 of 1 materials created" });
  expect(vi.mocked(materialCreationClient.create).mock.calls[0][0]).toEqual(vi.mocked(materialCreationClient.create).mock.calls[1][0]);
});
it.each(["empty", "unavailable"])("recovers the frozen request when templates are %s after reload", async availability => {
  vi.mocked(materialCreationClient.create).mockRejectedValueOnce(new TypeError("Lost response"));
  const view = setup(); await fill(); fireEvent.click(screen.getByRole("button", { name: "Create material" }));
  await screen.findByText(/result is unknown/);
  const original = vi.mocked(materialCreationClient.create).mock.calls[0][0];
  view.unmount();
  if (availability === "empty") vi.mocked(materialCreationClient.options).mockResolvedValue({ pathsVersion: 3, templates: [] });
  else vi.mocked(materialCreationClient.options).mockRejectedValue(new Error("Template root unavailable"));
  setup();
  const recover = await screen.findByRole("button", { name: "Recover / finish this batch" });
  expect(recover).toBeEnabled(); expect(requestNavigation("/materials")).toBe(false);
  fireEvent.click(recover);
  await screen.findByRole("heading", { name: "1 of 1 materials created" });
  expect(vi.mocked(materialCreationClient.create).mock.calls[1][0]).toEqual(original);
  expect(sessionStorage.getItem("reawote.material-create.current")).toBeNull();
});
it("rejects duplicate canonical names and an oversized batch", () => {
  expect(() => pastedMaterialNames("Orange tiles\nORANGE-TILES")).toThrow(/duplicate names/);
  expect(() => pastedMaterialNames(Array.from({ length: 101 }, (_, i) => `Name ${i}`).join("\n"))).toThrow(/1–100/);
});

it.each([false, true])("creates from names alone with missing templates (multiple=%s)", async multiple => {
  vi.mocked(materialCreationClient.options).mockResolvedValue({ pathsVersion: 0, templates: [], templatesAvailable: false, canCreateFolders: false });
  vi.mocked(materialCreationClient.create).mockResolvedValue({ ...result, items: [{ ...result.items[0], identity: null, folderPath: null }] });
  setup(); await screen.findByRole("form", { name: "Add material" });
  if (multiple) fireEvent.click(screen.getByRole("checkbox", { name: "Create multiple materials" }));
  fireEvent.change(screen.getByRole("textbox", { name: multiple ? /one Excel column/ : "Material name" }), { target: { value: multiple ? "First\nSecond" : "Only name" } });
  fireEvent.click(screen.getByRole("button", { name: multiple ? "Create materials" : "Create material" }));
  await screen.findByRole("heading", { name: "1 of 1 materials created" });
  expect(materialCreationClient.create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ published_brand_id: null,
    main_category_code: null, assigned_processor_id: null, project_id: null, template_name: null,
    names: multiple ? ["First", "Second"] : ["Only name"], category_ids: [], collection_ids: [] }));
  expect(screen.getByRole("link", { name: "NEW-MATERIAL" })).toHaveAttribute("href", "/materials/" + materialDto.id);
  expect(screen.getByText(/No data folder/)).toBeVisible();
});
