import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mockApiClient } from "../api/client";
import { catalogClient, type MaterialContent } from "../api/catalogClient";
import { metadataClient } from "../api/metadataClient";
import { materialFromDto, type Material } from "../api/materialDto";
import { SessionContext } from "../auth/context";
import { materialDto, processorDto } from "../test/materialFixtures";
import { brands } from "../api/mockData";
import { PublicationMaterialsTable } from "./PublicationMaterialsTable";

vi.mock("./MaterialsGrid", () => ({ MaterialThumbnail: ({ material }: { material: Material }) => <span>Thumbnail {material.materialName}</span> }));
vi.mock("./MaterialPreviewStrip", () => ({ MaterialPreviewStrip: ({ material, editable }: { material: Material; editable: boolean }) => <button aria-label={`Inspect previews ${material.materialName}`} data-editable={String(editable)}>Full-quality previews</button> }));
vi.mock("./MaterialLibraryBulkEditor", () => ({ MaterialLibraryBulkEditor: ({ materials, onChanged, includeWorkflow }: { materials: Material[]; onChanged: () => void; includeWorkflow: boolean }) => <div aria-label="Library bulk editor" data-workflow={String(includeWorkflow)}>{materials.map(row => row.materialName).join(",")}<button onClick={onChanged}>Save library values</button></div> }));
vi.mock("./MaterialBulkContent", () => ({ MaterialBulkContent: () => <button>Categories / collections</button> }));
vi.mock("./MaterialAiBriefDialog", () => ({ MaterialAiBriefDialog: ({ materials, onClose, onChanged }: { materials: Material[]; onClose: () => void; onChanged: () => void }) => <div role="dialog" aria-label="AI batch">{materials.map(row => row.materialName).join(",")}<button onClick={onChanged}>Apply AI data</button><button onClick={onClose}>Close AI</button></div> }));
vi.mock("./MaterialContentPanel", () => ({ MaterialContentPanel: () => <div>Library data fields</div> }));

const first = materialFromDto({ ...materialDto, material_name: "A-READY", workflow_status: "DONE", folder_path: "Customer/A-READY" });
const second = { ...first, id: "50000000-0000-4000-8000-000000000002", materialName: "B-MISSING" };
const third = { ...first, id: "50000000-0000-4000-8000-000000000003", materialName: "C-WARNING" };
const failed = { ...first, id: "50000000-0000-4000-8000-000000000004", materialName: "D-UNAVAILABLE" };
const selection = [first, second, third, failed];
const content: MaterialContent = { materialId: first.id, revision: 1, status: "MANUAL_DRAFT", description: "Woven fabric", tags: ["woven"], credits: 2,
  collections: [], categories: [{ id: "60000000-0000-4000-8000-000000000001", value: "Fabric", active: true, abbreviation: "F01", version: 1, brandId: null, createdAt: null }] };

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  vi.spyOn(mockApiClient, "getMaterial").mockImplementation(async id => selection.find(row => row.id === id)!);
  vi.spyOn(mockApiClient, "getBrands").mockResolvedValue([{ ...brands[1], id: first.publishedBrandId!, isActive: true, brandIdentifier: "synthetic" }]);
  vi.spyOn(catalogClient, "content").mockImplementation(async id => {
    if (id === failed.id) throw new Error("Disconnected");
    return { ...content, materialId: id, credits: id === second.id ? null : 2, description: id === third.id ? null : content.description };
  });
  vi.spyOn(metadataClient, "inspect").mockResolvedValue({ available: true, writesEnabled: true, editable: true, expectedUpdatedAt: first.updatedAt, sha256: "a".repeat(64), sourceStatus: "VALID", active: null, values: { hex_color: "#FFFFFF", width_cm: "10", height_cm: "20" } });
});
afterEach(() => vi.restoreAllMocks());
function setup() {
  const onChanged = vi.fn(), onBusyChange = vi.fn();
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <PublicationMaterialsTable materials={selection} client={mockApiClient} navigate={vi.fn()} preview={null} refreshKey={0} disabled={false} onChanged={onChanged} onBusyChange={onBusyChange} />
  </SessionContext.Provider>);
  return { onChanged, onBusyChange };
}
const row = (name: string) => screen.getByRole("row", { name: `Publication row ${name}` });

it("shows required data and warning rows, filters attention, and retains failed rows explicitly", async () => {
  setup(); await screen.findByRole("row", { name: "Publication row A-READY" });
  expect(row("A-READY")).toHaveClass("publication-data-ready");
  expect(row("B-MISSING")).toHaveClass("publication-data-error");
  expect(row("C-WARNING")).toHaveClass("publication-data-warning");
  expect(row("D-UNAVAILABLE")).toHaveTextContent("Data unavailable");
  fireEvent.change(screen.getByRole("combobox", { name: "Show" }), { target: { value: "issues" } });
  expect(screen.queryByRole("row", { name: "Publication row A-READY" })).not.toBeInTheDocument();
  expect(screen.getByText("0 selected · 3/4 shown")).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all visible publication materials" }));
  const bulk = screen.getByLabelText("Library bulk editor");
  expect(bulk).toHaveTextContent("B-MISSING,C-WARNING");
  expect(bulk).not.toHaveTextContent("D-UNAVAILABLE");
  expect(screen.getByRole("checkbox", { name: "Select publication material D-UNAVAILABLE" })).toBeDisabled();
  expect(screen.getByText(/Publication always includes all 4 materials/)).toBeVisible();
});

it("supports Shift ranges, Ctrl toggles, and AI for the explicitly checked subset", async () => {
  const { onBusyChange, onChanged } = setup(); await screen.findByRole("row", { name: "Publication row A-READY" });
  fireEvent.click(row("A-READY")); fireEvent.click(row("C-WARNING"), { shiftKey: true });
  fireEvent.click(row("B-MISSING"), { ctrlKey: true });
  fireEvent.click(screen.getByRole("button", { name: "Select highlighted (2)" }));
  expect(screen.getByLabelText("Library bulk editor")).toHaveTextContent("A-READY,C-WARNING");
  expect(screen.getByLabelText("Library bulk editor")).toHaveAttribute("data-workflow", "true");
  fireEvent.click(screen.getByRole("button", { name: "AI descriptions" }));
  const dialog = screen.getByRole("dialog", { name: "AI batch" });
  expect(dialog).toHaveTextContent("A-READY,C-WARNING"); expect(dialog).not.toHaveTextContent("B-MISSING");
  await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(true));
  expect(screen.getByRole("checkbox", { name: "Select all visible publication materials" })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Apply AI data" }));
  expect(onChanged).toHaveBeenCalledOnce();
  fireEvent.click(within(dialog).getByRole("button", { name: "Close AI" }));
  await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(false));
});

it("opens inspection previews and offers library editing without leaving the batch", async () => {
  const { onBusyChange } = setup(); await screen.findByRole("row", { name: "Publication row A-READY" });
  fireEvent.click(screen.getByRole("button", { name: "Expand publication previews" }));
  expect(screen.getByRole("button", { name: "Inspect previews A-READY" })).toHaveAttribute("data-editable", "false");
  fireEvent.click(within(row("A-READY")).getByRole("button", { name: "Edit library data" }));
  const dialog = await screen.findByRole("dialog", { name: "Publication data for A-READY" });
  expect(dialog).toHaveTextContent("Library data fields");
  await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(true));
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(false));
});

it("refreshes row values and invalidates publication review after bulk edits", async () => {
  const { onChanged } = setup(); await screen.findByRole("row", { name: "Publication row A-READY" });
  fireEvent.click(screen.getByRole("checkbox", { name: "Select publication material B-MISSING" }));
  vi.mocked(catalogClient.content).mockImplementation(async id => ({ ...content, materialId: id }));
  fireEvent.click(screen.getByRole("button", { name: "Save library values" }));
  await waitFor(() => expect(row("B-MISSING")).toHaveClass("publication-data-ready"));
  expect(onChanged).toHaveBeenCalledOnce();
});

it("does not treat unavailable source metadata or an inactive customer as publication-ready", async () => {
  const source = await metadataClient.inspect(first.id);
  vi.mocked(metadataClient.inspect).mockResolvedValue({ ...source, available: false, sourceStatus: "UNAVAILABLE", sha256: null });
  vi.mocked(mockApiClient.getBrands).mockResolvedValue([{ ...brands[1], id: first.publishedBrandId!, isActive: false, brandIdentifier: "" }]);
  setup(); await screen.findByRole("row", { name: "Publication row A-READY" });
  expect(row("A-READY")).toHaveClass("publication-data-error");
  expect(row("A-READY")).toHaveTextContent("Check and save source metadata");
  expect(row("A-READY")).toHaveTextContent("Set an active Customer and brand identifier");
});
