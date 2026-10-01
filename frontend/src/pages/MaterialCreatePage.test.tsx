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
  fireEvent.click(screen.getByRole("checkbox", { name: "F03 · Upholstery" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Collection" }));
  expect(screen.getByRole("checkbox", { name: "F · Fabrics (Main category)" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Create material" }));
  await screen.findByRole("heading", { name: "1 of 1 folders created" });
  expect(materialCreationClient.create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ project_id: null, published_brand_id: customer.id,
    names: ["New material"], resolution: 8, template_name: "base.sbs", expected_paths_version: 2, category_ids: [extra.id], collection_ids: [collection.id] }));
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
  fireEvent.change(screen.getByRole("spinbutton", { name: "Resolution (K)" }), { target: { value: "12" } });
  fireEvent.click(screen.getByRole("button", { name: "Create materials" }));
  await waitFor(() => expect(materialCreationClient.create).toHaveBeenCalledWith(expect.objectContaining({ names: ["Orange tiles", "Yellow tiles"], resolution: 12 })));
});
it("rejects several pasted Excel columns without creating records", async () => {
  setup(); await fill(); fireEvent.click(screen.getByRole("checkbox", { name: "Create multiple materials" }));
  fireEvent.change(screen.getByRole("textbox", { name: /one Excel column/ }), { target: { value: "Orange\tTiles\nYellow\tTiles" } });
  fireEvent.click(screen.getByRole("button", { name: "Create materials" }));
  expect(screen.getByRole("alert")).toHaveTextContent("one Excel column only"); expect(materialCreationClient.create).not.toHaveBeenCalled();
});
it("rejects a resolution above the supported packaging master limit before creating records", async () => {
  setup(); await fill();
  expect(screen.getByRole("spinbutton", { name: "Resolution (K)" })).toHaveAttribute("max", "32");
  fireEvent.change(screen.getByRole("spinbutton", { name: "Resolution (K)" }), { target: { value: "33" } });
  fireEvent.click(screen.getByRole("button", { name: "Create material" }));
  expect(screen.getByRole("alert")).toHaveTextContent("from 1 to 32K");
  expect(materialCreationClient.create).not.toHaveBeenCalled();
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
  await screen.findByRole("heading", { name: "1 of 1 folders created" });
  expect(materialCreationClient.create).toHaveBeenCalledTimes(2);
  expect(vi.mocked(materialCreationClient.create).mock.calls[1][0]).toEqual(first);
});
it("shows per-item partial outcome and resumes the same batch without editing common fields", async () => {
  vi.mocked(materialCreationClient.create).mockResolvedValueOnce({ ...result, status: "PARTIAL", completedCount: 0, items: [{ ...result.items[0], status: "FAILED", errorCode: "MATERIAL_FOLDER_EXISTS" }] });
  setup(); await fill(); fireEvent.click(screen.getByRole("button", { name: "Create material" }));
  await screen.findByText(/FAILED: MATERIAL_FOLDER_EXISTS/);
  expect(screen.getByLabelText("Material name")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Recover / finish this batch" }));
  await screen.findByRole("heading", { name: "1 of 1 folders created" });
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
  await screen.findByRole("heading", { name: "1 of 1 folders created" });
  expect(vi.mocked(materialCreationClient.create).mock.calls[1][0]).toEqual(original);
  expect(sessionStorage.getItem("reawote.material-create.current")).toBeNull();
});
it("rejects duplicate canonical names and an oversized batch", () => {
  expect(() => pastedMaterialNames("Orange tiles\nORANGE-TILES")).toThrow(/duplicate names/);
  expect(() => pastedMaterialNames(Array.from({ length: 101 }, (_, i) => `Name ${i}`).join("\n"))).toThrow(/1–100/);
});
