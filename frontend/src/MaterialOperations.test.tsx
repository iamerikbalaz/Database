import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { materialFromDto } from "./api/materialDto";
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
function rename() {
  const close = vi.fn(), changed = vi.fn(async () => true);
  render(<MaterialNameDialog material={material} onClose={close} onChanged={changed} />);
  return { close, changed };
}
async function prepare() {
  fireEvent.change(screen.getByLabelText("Material name"), { target: { value: "New name" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview changes" }));
  await screen.findByText(plan.target.folder!);
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
it("requires a reviewed plan and explicit approval before renaming", async () => {
  const { changed, close } = rename(); await prepare();
  expect(screen.getByRole("button", { name: "Confirm rename" })).toBeDisabled();
  expect(identityClient.plan).toHaveBeenCalledWith(material.id, expect.objectContaining({ material_name: "New name", target_parent: "BRAND" }));
  fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(screen.getByRole("button", { name: "Confirm rename" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce()); expect(close).toHaveBeenCalledOnce();
  expect(identityClient.confirm).toHaveBeenCalledWith(material.id, expect.objectContaining({ material_name: "New name", expected_generation: 4, warnings_acknowledged: true }));
});
it("invalidates the reviewed plan if the new name changes", async () => {
  rename(); await prepare(); fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.change(screen.getByLabelText("Material name"), { target: { value: "Other name" } });
  expect(screen.getByRole("button", { name: "Confirm rename" })).toBeDisabled(); expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
});
it("retains the exact rename request after an unknown outcome", async () => {
  rename(); await prepare(); fireEvent.click(screen.getByRole("checkbox"));
  vi.mocked(identityClient.confirm).mockRejectedValueOnce(new TypeError("timeout"));
  fireEvent.click(screen.getByRole("button", { name: "Confirm rename" })); await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  const first = vi.mocked(identityClient.confirm).mock.calls[0][1];
  fireEvent.click(screen.getByRole("button", { name: "Recover recorded change" }));
  await waitFor(() => expect(identityClient.confirm).toHaveBeenCalledTimes(2));
  expect(vi.mocked(identityClient.confirm).mock.calls[1][1]).toBe(first);
});
it("does not permit writes when the source capability is disabled", async () => {
  vi.mocked(identityClient.operations).mockResolvedValue({ enabled: false, items: [] });
  rename(); fireEvent.change(screen.getByLabelText("Material name"), { target: { value: "New name" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview changes" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("disabled"); expect(identityClient.plan).not.toHaveBeenCalled();
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
