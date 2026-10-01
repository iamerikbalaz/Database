import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { catalogClient, type CatalogValue, type MaterialContent } from "../api/catalogClient";
import { materialBulkContentClient } from "../api/materialCreationClient";
import { materialFromDto } from "../api/materialDto";
import { materialDto } from "../test/materialFixtures";
import { MaterialBulkContent } from "./MaterialBulkContent";

const material = materialFromDto(materialDto), second = { ...material, id: "50000000-0000-4000-8000-000000000002" };
const category = { id: "70000000-0000-4000-8000-000000000001", value: "Extra category", abbreviation: "G02", version: 1, active: true, brandId: null, createdAt: null } satisfies CatalogValue;
const collection = { ...category, id: "70000000-0000-4000-8000-000000000002", value: "Collection", brandId: material.publishedBrandId };
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  vi.spyOn(catalogClient, "categories").mockResolvedValue([category]); vi.spyOn(catalogClient, "collections").mockResolvedValue([collection]);
  vi.spyOn(catalogClient, "content").mockImplementation(async id => ({ materialId: id, revision: 3 } as MaterialContent));
  vi.spyOn(materialBulkContentClient, "add").mockResolvedValue({ updatedCount: 2 });
});
afterEach(() => vi.restoreAllMocks());
it("adds only explicit selected IDs and revisions and closes after atomic save", async () => {
  const changed = vi.fn();
  render(<MaterialBulkContent materials={[second]} actionTarget={null} onChanged={changed} onBusyChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Categories / collections" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "G02 · Extra category" }));
  fireEvent.click(screen.getByText(/^Brand collections/, { selector: "summary" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Collection" }));
  fireEvent.click(screen.getByRole("button", { name: "Add to selected materials" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(materialBulkContentClient.add).toHaveBeenCalledExactlyOnceWith({ idempotency_key: expect.any(String), materials: [{ id: second.id, expected_updated_at: second.updatedAt, expected_revision: 3 }], category_ids: [category.id], collection_ids: [collection.id] });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("allows categories but does not offer collections for mixed Customers", async () => {
  render(<MaterialBulkContent materials={[material, { ...second, publishedBrandId: "30000000-0000-4000-8000-000000000003" }]} actionTarget={null} onChanged={vi.fn()} onBusyChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Categories / collections" }));
  await screen.findByText(/Collections require all selected/);
  expect(catalogClient.collections).not.toHaveBeenCalled();
  expect(screen.queryByRole("checkbox", { name: "Collection" })).not.toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "G02 · Extra category" })).toBeEnabled();
});
it("recovers an uncertain save with the same key and prevents closing", async () => {
  vi.mocked(materialBulkContentClient.add).mockRejectedValueOnce(new TypeError("Lost reply"));
  render(<MaterialBulkContent materials={[material]} actionTarget={null} onChanged={vi.fn()} onBusyChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Categories / collections" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "G02 · Extra category" }));
  fireEvent.click(screen.getByRole("button", { name: "Add to selected materials" }));
  const recover = await screen.findByRole("button", { name: "Recover bulk content save" });
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  fireEvent.click(recover);
  await waitFor(() => expect(materialBulkContentClient.add).toHaveBeenCalledTimes(2));
  expect(vi.mocked(materialBulkContentClient.add).mock.calls[0][0]).toEqual(vi.mocked(materialBulkContentClient.add).mock.calls[1][0]);
});
it("does not offer empty or oversized selections", () => {
  const view = render(<MaterialBulkContent materials={[]} actionTarget={null} onChanged={vi.fn()} onBusyChange={vi.fn()} />);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  view.rerender(<MaterialBulkContent materials={Array.from({length:101}, () => material)} actionTarget={null} onChanged={vi.fn()} onBusyChange={vi.fn()} />);
  expect(screen.getByRole("button")).toBeDisabled();
});
it("waits for its dock and preserves an open dialog when the selected-only dock disappears", async () => {
  const changed = vi.fn(), busy = vi.fn(), target = document.createElement("div");
  document.body.append(target);
  try {
    const view = render(<MaterialBulkContent dockOnly materials={[material]} actionTarget={null} onChanged={changed} onBusyChange={busy} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    view.rerender(<MaterialBulkContent dockOnly materials={[material]} actionTarget={target} onChanged={changed} onBusyChange={busy} />);
    const action = screen.getByRole("button", { name: "Categories / collections" });
    expect(target).toContainElement(action);
    fireEvent.click(action);
    fireEvent.click(await screen.findByRole("checkbox", { name: "G02 · Extra category" }));
    view.rerender(<MaterialBulkContent dockOnly materials={[]} actionTarget={null} onChanged={changed} onBusyChange={busy} />);
    expect(screen.queryByRole("button", { name: "Categories / collections" })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "G02 · Extra category" })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Add to selected materials" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(materialBulkContentClient.add).toHaveBeenCalledWith(expect.objectContaining({ materials: [{ id: material.id, expected_updated_at: material.updatedAt, expected_revision: 3 }] }));
  } finally { target.remove(); }
});
