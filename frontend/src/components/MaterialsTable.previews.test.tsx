import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialsTable } from "./MaterialsTable";
import { mockApiClient } from "../api/client";
import { GalleryStore } from "../api/galleryStore";
import { materialFromDto } from "../api/materialDto";
import { materialDto, processorDto } from "../test/materialFixtures";
import { SessionContext } from "../auth/context";
import { previewEditClient } from "../api/previewEditClient";
import { identityClient, type IdentityPlan, type IdentityOperation } from "../api/identityClient";
import { materialTableClient } from "../api/materialTableClient";

const material = { ...materialFromDto(materialDto), folderPath: "TEST/TEST_0001_SAMPLE_K03" };
beforeEach(() => {
  localStorage.clear(); HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  vi.spyOn(previewEditClient, "pending").mockResolvedValue([]);
  vi.spyOn(previewEditClient, "apply");
});
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });
function mount(row = material) {
  const store = new GalleryStore();
  vi.spyOn(store, "listing").mockResolvedValue({ missing: false, ignoredEntries: 0, items: ["SPHERE_1.png", "SPHERE_2.png"].map(name => ({ name, size: 50, sha256: "a".repeat(64) })) });
  vi.spyOn(store, "image").mockImplementation(() => new Promise(() => {}));
  const client = { ...mockApiClient, getMaterial: vi.fn().mockResolvedValue(row) };
  const prepare = vi.fn(), check = vi.fn();
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsTable materials={[row]} store={store} client={client} projects={[]} brands={[]} users={[]} categories={[{ code: "K03", value: "Metal" }]}
      navigate={vi.fn()} refresh={vi.fn()} onBusyChange={vi.fn()} onPreparePublication={prepare} onCheckSelected={check} />
  </SessionContext.Provider>);
  return { store, client, prepare, check };
}
it("puts the optional Number directly between Preview and Material", () => {
  localStorage.setItem("materials.columns.v1", JSON.stringify([{ key: "number", visible: true }]));
  mount(); const headers = screen.getAllByRole("columnheader");
  expect(headers[1]).toHaveTextContent("Preview"); expect(headers[2]).toHaveTextContent("Number"); expect(headers[3]).toHaveTextContent("Material");
  const cells = within(screen.getByRole("row", { name: `Material row ${material.materialName}` })).getAllByRole("cell");
  expect(cells[2]).toHaveTextContent(String(material.sequenceNumber).padStart(4, "0"));
});
it("keeps source actions inside the selected-only bulk group", () => {
  const { prepare } = mount();
  expect(screen.queryByRole("button", { name: /Prepare for publication/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Edit previews" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all visible materials" }));
  const group = screen.getByRole("group", { name: "Apply to 1 selected materials" });
  expect(within(group).getByRole("button", { name: "Edit previews" })).toBeVisible();
  expect(within(group).getByRole("button", { name: /Auto-check/ })).toBeVisible();
  fireEvent.click(within(group).getByRole("button", { name: /Prepare for publication/ })); expect(prepare).toHaveBeenCalledWith([material]);
});
it("expands all previews and opens rename only on filename double-click", async () => {
  mount(); fireEvent.click(screen.getByRole("button", { name: "Expand previews" }));
  const filename = await screen.findByRole("button", { name: "SPHERE_2.png" });
  expect(screen.getByRole("button", { name: "SPHERE_1.png" })).toBeVisible();
  fireEvent.click(filename); expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.doubleClick(filename); expect(screen.getByRole("dialog", { name: "Rename preview" })).toBeVisible();
  expect(screen.getByLabelText("New filename")).toHaveValue("SPHERE_2.png"); expect(previewEditClient.apply).not.toHaveBeenCalled();
});
it("opens the existing confirmed material rename dialog from the pencil", async () => {
  mount(); fireEvent.click(screen.getByRole("button", { name: `Edit name of ${material.materialName}` }));
  expect(screen.getByRole("dialog", { name: "Edit Name" })).toBeVisible();
  expect(screen.getByLabelText("Material name")).toHaveValue(material.materialName.toUpperCase());
});
it("does not bypass source-rename safety through bulk Main category for a Done material", async () => {
  const row = { ...material, mainCategoryCode: "A01", workflowStatus: "DONE" as const };
  const plan = vi.spyOn(identityClient, "plan"), update = vi.spyOn(materialTableClient, "update");
  mount(row); fireEvent.click(screen.getByRole("checkbox", { name: "Select all visible materials" }));
  fireEvent.change(screen.getByLabelText("Property"), { target: { value: "main_category_code" } });
  fireEvent.click(screen.getByRole("button", { name: "Review bulk change" }));
  fireEvent.click(screen.getByRole("button", { name: "Apply change" }));
  await waitFor(() => expect(screen.getByText(/Clear Published and set Status to In progress/)).toBeVisible());
  expect(plan).not.toHaveBeenCalled(); expect(update).not.toHaveBeenCalled();
});
it("uses the confirmed source plan for bulk Main category and recovers the exact request after a lost reply", async () => {
  const row = { ...material, mainCategoryCode: "A01", workflowStatus: "IN_PROGRESS" as const, isPublished: false };
  const plan = vi.spyOn(identityClient, "plan").mockResolvedValue({ ready: true, errors: [], generation: 4, hash: "a".repeat(64) } as unknown as IdentityPlan);
  const confirm = vi.spyOn(identityClient, "confirm").mockRejectedValueOnce(new TypeError("Lost reply")).mockResolvedValue({ id: "00000000-0000-4000-8000-000000000097", status: "COMPLETED" } as unknown as IdentityOperation);
  const update = vi.spyOn(materialTableClient, "update");
  const { client } = mount(row);
  client.getMaterial.mockResolvedValueOnce(row).mockResolvedValue({ ...row, mainCategoryCode: "K03", updatedAt: "2026-10-01T15:00:00Z" });
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all visible materials" }));
  fireEvent.change(screen.getByLabelText("Property"), { target: { value: "main_category_code" } });
  fireEvent.click(screen.getByRole("button", { name: "Review bulk change" }));
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Apply change" }));
  fireEvent.click(await screen.findByRole("button", { name: "Retry same request and continue" }));
  await screen.findByText("Main category and source folder updated");
  expect(plan).toHaveBeenCalledOnce(); expect(confirm).toHaveBeenCalledTimes(2);
  expect(confirm.mock.calls[1]).toEqual(confirm.mock.calls[0]);
  expect(confirm.mock.calls[0][1]).toMatchObject({ main_category_code: "K03", expected_generation: 4, expected_proposal_hash: "a".repeat(64), target_parent: "TEST" });
  expect(update).not.toHaveBeenCalled();
});
it("does not plan a folder rename when the selected material changed since review", async () => {
  const row = { ...material, mainCategoryCode: "A01", workflowStatus: "IN_PROGRESS" as const, isPublished: false };
  const plan = vi.spyOn(identityClient, "plan"); const { client } = mount(row);
  client.getMaterial.mockResolvedValue({ ...row, updatedAt: "2026-10-01T15:00:00Z" });
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all visible materials" }));
  fireEvent.change(screen.getByLabelText("Property"), { target: { value: "main_category_code" } });
  fireEvent.click(screen.getByRole("button", { name: "Review bulk change" })); fireEvent.click(screen.getByRole("button", { name: "Apply change" }));
  await screen.findByText("Material changed. Refresh before changing Main category."); expect(plan).not.toHaveBeenCalled();
});
