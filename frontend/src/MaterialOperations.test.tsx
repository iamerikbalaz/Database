import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { materialFromDto, type Material } from "./api/materialDto";
import { materialLocalClient } from "./api/materialLocalClient";
import { identityClient, type IdentityPlan } from "./api/identityClient";
import { MaterialDataFolder, MaterialDataCheck } from "./components/MaterialDataFolder";
import { MaterialNameDialog } from "./components/MaterialNameDialog";
import { SessionContext } from "./auth/context";
import { materialDto, processorDto } from "./test/materialFixtures";

const material = materialFromDto({ ...materialDto, folder_path: "BRAND/BRAND_9999_CRYSTAL_G03" });
const source = { materialId: material.id, identity: material.technicalIdentity, folder: material.folderPath, brandId: material.publishedBrandId, category: "G03", number: 9999, name: material.materialName };
const plan: IdentityPlan = { source, target: { ...source, name: "New name", folder: "BRAND/BRAND_9999_NEW-NAME_G03" }, generation: 4, hash: "a".repeat(64), reservesNumber: false,
  workerHash: "b".repeat(64), sourceHash: "c".repeat(64), ready: true, errors: [], warnings: [], metadata: { beforeHash: null, afterHash: null, fields: ["PRODUCT_NAME"] },
  changes: [{ source: "4K/OLD_COL.jpg", target: "4K/NEW_COL.jpg", kind: "file", hash: "d".repeat(64) }] };
beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value() { this.setAttribute("open", ""); } });
  vi.spyOn(materialLocalClient, "info").mockResolvedValue({ absolutePath: "C:\\Users\\Admin\\Desktop\\Test_data\\BRAND\\BRAND_9999_CRYSTAL_G03", canOpen: true, canMove: true });
  vi.spyOn(materialLocalClient, "openFolder").mockResolvedValue(undefined);
  vi.spyOn(materialLocalClient, "destination").mockResolvedValue({ parent: null, absolutePath: null });
  vi.spyOn(identityClient, "operations").mockResolvedValue({ enabled: true, items: [] });
  vi.spyOn(identityClient, "plan").mockResolvedValue(plan);
  vi.spyOn(identityClient, "confirm").mockResolvedValue({ id: processorDto.id, status: "COMPLETED", source, target: plan.target, reason: "Rename material", createdAt: material.createdAt, actorId: processorDto.id, failure: null });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ material_id: material.id, path: "", omitted_entries: 0, entries: [] }))));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function folder() {
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialDataFolder material={material} onChanged={vi.fn(async () => true)} />
  </SessionContext.Provider>);
}
function rename(change: Partial<Material> = {}, destination?: { parent: string; absolutePath: string }) {
  const close = vi.fn(), changed = vi.fn(async () => true);
  render(<MaterialNameDialog material={{ ...material, ...change }} destination={destination} onClose={close} onChanged={changed} />);
  return { close, changed };
}
function enterName() {
  fireEvent.change(screen.getByLabelText("Material name"), { target: { value: "New name" } });
}
it("shows the authorized absolute path and opens Explorer only after user action", async () => {
  folder(); const path = await screen.findByLabelText("Absolute folder path");
  await waitFor(() => expect((path as HTMLInputElement).value).toContain("Test_data"));
  expect(path).toHaveAttribute("readonly"); expect(materialLocalClient.openFolder).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Open folder" }));
  await waitFor(() => expect(materialLocalClient.openFolder).toHaveBeenCalledWith(material.id));
});
it("does not start a move when the native picker is cancelled", async () => {
  folder(); fireEvent.click(await screen.findByRole("button", { name: "Choose destination folder…" }));
  await waitFor(() => expect(materialLocalClient.destination).toHaveBeenCalledOnce());
  expect(identityClient.plan).not.toHaveBeenCalled(); expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("checks the source internally and renames after one explicit confirmation", async () => {
  const { changed, close } = rename();
  expect(screen.getByRole("button", { name: "Confirm rename" })).toBeDisabled();
  enterName(); expect(screen.getByLabelText("Material name")).toHaveValue("NEW NAME");
  expect(screen.queryByRole("button", { name: "Preview changes" })).not.toBeInTheDocument();
  expect(identityClient.plan).not.toHaveBeenCalled(); expect(identityClient.confirm).not.toHaveBeenCalled();
  expect(screen.getByText(/renames the material folder/)).toHaveTextContent("Checked resets to no");
  fireEvent.click(screen.getByRole("button", { name: "Confirm rename" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce()); expect(close).toHaveBeenCalledOnce();
  expect(identityClient.plan).toHaveBeenCalledWith(material.id, expect.objectContaining({ material_name: "NEW NAME", target_parent: "BRAND" }));
  expect(identityClient.confirm).toHaveBeenCalledWith(material.id, expect.objectContaining({ material_name: "NEW NAME", expected_generation: 4, expected_proposal_hash: plan.hash, warnings_acknowledged: true }));
});
it("does not write a blocked plan and repeats checks for a corrected name", async () => {
  vi.mocked(identityClient.plan).mockResolvedValueOnce({ ...plan, ready: false, errors: [{ code: "DESTINATION_EXISTS", path: "BRAND/TAKEN" }] });
  rename(); enterName(); fireEvent.click(screen.getByRole("button", { name: "Confirm rename" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("conflicts");
  expect(screen.getByText(/DESTINATION EXISTS/)).toBeVisible(); expect(identityClient.confirm).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Material name"), { target: { value: "Other name" } });
  expect(screen.queryByText(/DESTINATION EXISTS/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Confirm rename" }));
  await waitFor(() => expect(identityClient.confirm).toHaveBeenCalledOnce());
  expect(identityClient.plan).toHaveBeenLastCalledWith(material.id, expect.objectContaining({ material_name: "OTHER NAME" }));
});
it("retains the exact rename request after an unknown outcome", async () => {
  rename(); enterName();
  vi.mocked(identityClient.confirm).mockRejectedValueOnce(new TypeError("timeout"));
  fireEvent.click(screen.getByRole("button", { name: "Confirm rename" })); await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(screen.queryByRole("region", { name: "Folder change preview" })).not.toBeInTheDocument();
  expect(screen.queryByText("Show files affected")).not.toBeInTheDocument();
  const first = vi.mocked(identityClient.confirm).mock.calls[0][1];
  fireEvent.click(screen.getByRole("button", { name: "Recover recorded change" }));
  await waitFor(() => expect(identityClient.confirm).toHaveBeenCalledTimes(2));
  expect(vi.mocked(identityClient.confirm).mock.calls[1][1]).toBe(first);
  expect(identityClient.plan).toHaveBeenCalledOnce();
});
it("does not permit writes when the source capability is disabled", async () => {
  vi.mocked(identityClient.operations).mockResolvedValue({ enabled: false, items: [] });
  rename(); fireEvent.change(screen.getByLabelText("Material name"), { target: { value: "New name" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm rename" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("disabled"); expect(identityClient.plan).not.toHaveBeenCalled();
});
it("allows an unpublished Done material name change without reopening its status", async () => {
  const { changed } = rename({ workflowStatus: "DONE" }); enterName();
  expect(screen.getByRole("button", { name: "Confirm rename" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm rename" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(identityClient.confirm).toHaveBeenCalledWith(material.id, expect.objectContaining({ material_name: "NEW NAME" }));
});
it("continues to block a published material rename", () => {
  rename({ isPublished: true, workflowStatus: "DONE" });
  expect(screen.getByLabelText("Material name")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Confirm rename" })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("Clear Published"); expect(identityClient.plan).not.toHaveBeenCalled();
});
it("continues to require In progress for moving a Done material", () => {
  rename({ workflowStatus: "DONE" }, { parent: "OTHER", absolutePath: "C:\\Test_data\\OTHER" });
  expect(screen.getByRole("button", { name: "Confirm move" })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("In progress"); expect(identityClient.plan).not.toHaveBeenCalled();
});
it("keeps a planning failure retryable without pretending a rename may have happened", async () => {
  vi.mocked(identityClient.plan).mockRejectedValueOnce(new TypeError("worker unavailable"));
  rename(); enterName(); fireEvent.click(screen.getByRole("button", { name: "Confirm rename" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("No rename was requested");
  expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
  expect(identityClient.confirm).not.toHaveBeenCalled();
});
it("checks data on demand and displays the returned issues and report", async () => {
  vi.spyOn(materialLocalClient, "check").mockResolvedValue({ issues: ["No PREVIEW folder"], report: "Basic source checks complete. No files were modified." });
  render(<MaterialDataCheck materialId={material.id} />); expect(materialLocalClient.check).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Check material data" }));
  expect((await screen.findByRole("textbox", { name: "Issues and report" }) as HTMLTextAreaElement).value).toContain("No PREVIEW folder");
});
it("prevents duplicate checks while one is running", async () => {
  let resolve!: (value: { report: string; issues: string[] }) => void;
  vi.spyOn(materialLocalClient, "check").mockImplementation(() => new Promise(done => { resolve = done; }));
  render(<MaterialDataCheck materialId={material.id} />);
  const button = screen.getByRole("button", { name: "Check material data" });
  act(() => { button.click(); button.click(); }); expect(materialLocalClient.check).toHaveBeenCalledOnce();
  await act(async () => resolve({ issues: [], report: "Complete" }));
});
