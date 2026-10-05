import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialLibraryBulkEditor } from "./MaterialLibraryBulkEditor";
import { catalogClient, type MaterialContent } from "../api/catalogClient";
import { metadataClient } from "../api/metadataClient";
import { mockApiClient } from "../api/client";
import { materialFromDto } from "../api/materialDto";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import { materialDto, processorDto } from "../test/materialFixtures";
import { requestNavigation } from "../navigationGuard";
import { ApiError } from "../api/errors";
import { libraryChange, prepareLibraryChange, type LibraryField } from "../api/materialLibraryClient";
import { materialTableClient } from "../api/materialTableClient";

const first = materialFromDto(materialDto), second = { ...first, id: "00000000-0000-4000-8000-000000000099", materialName: "Second" };
const source = { available: true, writesEnabled: true, editable: true, expectedUpdatedAt: first.updatedAt, sha256: "a".repeat(64), sourceStatus: "VALID", active: null, values: { hex_color: "#FFFFFF", width_cm: "12.1234", height_cm: "20" } };
const content: MaterialContent = { materialId: first.id, revision: 3, description: "Keep", credits: 5, tags: ["matte"], categories: [], collections: [], status: "MANUAL_DRAFT" };
const body = (id: string, tags = ["matte", "stone"]) => ({ material_id: id, revision: 4, description: "Keep", credits: 5, tags, categories: [], collections: [], content_status: "MANUAL_DRAFT" });

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  vi.spyOn(mockApiClient, "getMaterial").mockImplementation(async id => id === first.id ? first : second);
  vi.spyOn(catalogClient, "content").mockImplementation(async id => ({ ...content, materialId: id }));
  vi.spyOn(metadataClient, "inspect").mockResolvedValue(source);
  setSessionToken("t".repeat(43));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); setSessionToken(null); });
function setup(field: LibraryField | undefined, materials = [first, second], role: "ADMIN" | "LEADERSHIP" = "ADMIN", includeWorkflow = false) {
  const changed = vi.fn(), busy = vi.fn();
  const tree = (rows = materials) => <SessionContext.Provider value={{ session: { user: { ...processorDto, role }, csrf_token: "t".repeat(43), must_change_password: false }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialLibraryBulkEditor materials={rows} client={mockApiClient} field={field} onChanged={changed} onBusyChange={busy} includeWorkflow={includeWorkflow} />
  </SessionContext.Provider>;
  return { ...render(tree()), tree, changed, busy };
}
async function review() {
  fireEvent.click(screen.getByRole("button", { name: "Review bulk change" }));
  const dialog = await screen.findByRole("dialog", { name: "Review library changes" });
  await waitFor(() => expect(within(dialog).getByRole("button", { name: "Apply library changes" })).toBeEnabled());
  return dialog;
}
it("appends tags to the frozen selection and retries only the exact unknown request", async () => {
  const fetch = vi.fn().mockRejectedValueOnce(new TypeError("Disconnected")).mockResolvedValueOnce(new Response(JSON.stringify(body(first.id)))).mockResolvedValueOnce(new Response(JSON.stringify(body(second.id))));
  vi.stubGlobal("fetch", fetch);
  const app = setup("tags");
  fireEvent.change(screen.getByLabelText("Tags to append"), { target: { value: "MATTE, stone" } });
  const dialog = await review(); app.rerender(app.tree([second]));
  expect(within(dialog).getByText(first.materialName)).toBeVisible();
  fireEvent.click(within(dialog).getByRole("button", { name: "Apply library changes" }));
  const retry = await within(dialog).findByRole("button", { name: "Retry same request and continue" });
  expect(requestNavigation("/orders")).toBe(false);
  expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled();
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(retry);
  await within(dialog).findByText("2 saved · 0 rejected");
  expect(fetch.mock.calls[1]).toEqual(fetch.mock.calls[0]);
  const payload = JSON.parse(fetch.mock.calls[0][1].body);
  expect(payload).toMatchObject({ expected_updated_at: first.updatedAt, expected_revision: 3, field: "tags", tags: ["MATTE", "stone"] });
  expect(payload).not.toHaveProperty("description");
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" })); expect(app.changed).toHaveBeenCalledOnce();
  expect(requestNavigation("/orders")).toBe(true);
});
it("preserves source color for sample-size changes and resumes a durable running operation", async () => {
  const operation = "00000000-0000-4000-8000-000000000077";
  const save = vi.spyOn(metadataClient, "save").mockResolvedValue({ id: operation, status: "RUNNING", failure: null });
  const resume = vi.spyOn(metadataClient, "resume").mockResolvedValue({ id: operation, status: "COMPLETED", failure: null });
  setup("sample_size", [first]);
  fireEvent.change(screen.getByLabelText("Sample width (cm)"), { target: { value: "15.1" } });
  fireEvent.change(screen.getByLabelText("Sample height (cm)"), { target: { value: "20" } });
  const dialog = await review();
  fireEvent.click(within(dialog).getByRole("button", { name: "Apply library changes" }));
  fireEvent.click(await within(dialog).findByRole("button", { name: "Retry same request and continue" }));
  await within(dialog).findByText("1 saved · 0 rejected");
  expect(save).toHaveBeenCalledOnce();
  expect(save.mock.calls[0][1]).toMatchObject({ expected_updated_at: first.updatedAt, expected_sha256: source.sha256, values: { hex_color: "#FFFFFF", width_cm: "15.1", height_cm: "20" } });
  expect(save.mock.calls[0][1]).not.toHaveProperty("content");
  expect(resume).toHaveBeenCalledWith(first.id, operation);
});
it("does not round independent sample values when changing only color", async () => {
  const prepared = await prepareLibraryChange(first, { field: "color", color: "#000000" }, mockApiClient);
  expect(prepared.packet).toMatchObject({ kind: "metadata", body: { values: { hex_color: "#000000", width_cm: "12.1234", height_cm: "20" } } });
});
it("allows color changes while both sample dimensions are still missing", async () => {
  vi.mocked(metadataClient.inspect).mockResolvedValueOnce({ ...source, sourceStatus: "MISSING", sha256: null, values: { hex_color: null, width_cm: null, height_cm: null } });
  const prepared = await prepareLibraryChange(first, { field: "color", color: "#000000" }, mockApiClient);
  expect(prepared.packet).toMatchObject({ kind: "metadata", body: { expected_sha256: null, values: { hex_color: "#000000", width_cm: null, height_cm: null } } });
});
it("rejects stale or unavailable rows before any mutation", async () => {
  vi.mocked(mockApiClient.getMaterial).mockResolvedValueOnce({ ...first, updatedAt: "2026-10-05T12:00:00Z" });
  vi.spyOn(metadataClient, "save");
  setup("credits", [first]); fireEvent.change(screen.getByLabelText("Credits"), { target: { value: "8" } });
  fireEvent.click(screen.getByRole("button", { name: "Review bulk change" }));
  await screen.findByText("Material changed or is archived. Refresh before editing.");
  expect(screen.queryByRole("button", { name: "Apply library changes" })).not.toBeInTheDocument();
  expect(metadataClient.save).not.toHaveBeenCalled();
  vi.mocked(metadataClient.inspect).mockResolvedValueOnce({ ...source, writesEnabled: false });
  await expect(prepareLibraryChange(first, { field: "color", color: "#000000" }, mockApiClient)).rejects.toThrow(/source writes/);
});
it("does not dismiss uncertain outcomes after a later permission rejection", async () => {
  const save = vi.spyOn(metadataClient, "save").mockRejectedValueOnce(new TypeError("Lost")).mockRejectedValueOnce(new ApiError(403, "Forbidden"));
  setup("color", [first]); const dialog = await review();
  fireEvent.click(within(dialog).getByRole("button", { name: "Apply library changes" }));
  fireEvent.click(await within(dialog).findByRole("button", { name: "Retry same request and continue" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
  expect(save.mock.calls[1]).toEqual(save.mock.calls[0]);
  expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled();
});
it("stops delayed read completion on unmount and never starts a mutation", async () => {
  let resolve!: (value: typeof first) => void;
  vi.mocked(mockApiClient.getMaterial).mockReturnValueOnce(new Promise(done => { resolve = done; }));
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  const app = setup("credits", [first]);
  fireEvent.click(screen.getByRole("button", { name: "Review bulk change" })); app.unmount();
  await act(async () => resolve(first)); expect(fetch).not.toHaveBeenCalled();
});
it("enforces permissions, batch size and one-decimal sample inputs", () => {
  setup("credits", [first], "LEADERSHIP"); expect(screen.getByRole("button", { name: "Review bulk change" })).toBeDisabled();
  expect(() => libraryChange("sample_size", "1.25", "2")).toThrow(/one decimal/);
  expect(() => libraryChange("credits", "-1", "")).toThrow(/nonnegative/);
  expect(libraryChange("credits", "0", "")).toEqual({ field: "credits", credits: 0 });
  expect(libraryChange("tags", "stone, STONE, matte", "")).toEqual({ field: "tags", tags: ["stone", "matte"] });
});
it("limits bulk library changes to 100 selected records", () => {
  setup("credits", Array.from({ length: 101 }, (_, index) => ({ ...first, id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}` })));
  expect(screen.getByRole("button", { name: "Review bulk change" })).toBeDisabled();
});
it("optionally exposes workflow fields and uses the exact table command on retry", async () => {
  const update = vi.spyOn(materialTableClient, "update").mockRejectedValueOnce(new TypeError("Lost response")).mockResolvedValueOnce({ ...first, workflowStatus: "DONE" });
  setup(undefined, [first], "ADMIN", true);
  const property = screen.getByRole("combobox", { name: "Property" });
  expect(within(property).getByRole("option", { name: "Checked" })).toBeInTheDocument();
  fireEvent.change(property, { target: { value: "workflow_status" } });
  expect(screen.getByRole("combobox", { name: "New value" })).toHaveValue("DONE");
  const dialog = await review();
  fireEvent.click(within(dialog).getByRole("button", { name: "Apply library changes" }));
  fireEvent.click(await within(dialog).findByRole("button", { name: "Retry same request and continue" }));
  await within(dialog).findByText("1 saved · 0 rejected");
  expect(update.mock.calls[0]).toEqual([first, { workflow_status: "DONE" }, expect.any(String)]);
  expect(update.mock.calls[1]).toEqual(update.mock.calls[0]);
});
it("keeps workflow options out of the default standalone library editor", () => {
  setup(undefined, [first]);
  const property = screen.getByRole("combobox", { name: "Property" });
  expect(within(property).queryByRole("option", { name: "Status" })).not.toBeInTheDocument();
  expect(within(property).queryByRole("option", { name: "Checked" })).not.toBeInTheDocument();
});
