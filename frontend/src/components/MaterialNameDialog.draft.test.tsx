import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialNameDialog } from "./MaterialNameDialog";
import { materialFromDto, type Material } from "../api/materialDto";
import { materialDto, processorDto } from "../test/materialFixtures";
import { SessionContext } from "../auth/context";
import { ApiError } from "../api/errors";
import { identityClient } from "../api/identityClient";
import { requestNavigation } from "../navigationGuard";
import { pendingRecordCommands } from "../forms/pendingRecordCommands";
import { setSessionToken } from "../auth/sessionTransport";

const draft: Material = { ...materialFromDto(materialDto), isDraft: true, folderPath: null, technicalIdentity: null,
  sequenceNumber: null, publishedBrandId: null, mainCategoryCode: null, assignedProcessorId: null };
beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value() { this.setAttribute("open", ""); } });
  vi.spyOn(identityClient, "plan"); vi.spyOn(identityClient, "confirm");
});
afterEach(() => { pendingRecordCommands.set(processorDto.id, null); vi.restoreAllMocks(); });
function mount(updateMaterial: ReturnType<typeof vi.fn>, changed = vi.fn().mockResolvedValue(true), close = vi.fn()) {
  const view = render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false,
    csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialNameDialog material={draft} client={{ updateMaterial }} onChanged={changed} onClose={close} />
  </SessionContext.Provider>);
  return { ...view, changed, close };
}
function submit() {
  fireEvent.change(screen.getByLabelText("Material name"), { target: { value: "new draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save name" }));
}

it("updates only the draft record name with a single idempotency key and prevents double submit", async () => {
  let finish!: (material: Material) => void;
  const update = vi.fn().mockImplementation(() => new Promise<Material>(resolve => { finish = resolve; }));
  const { changed, close } = mount(update);
  fireEvent.change(screen.getByLabelText("Material name"), { target: { value: "new draft" } });
  const button = screen.getByRole("button", { name: "Save name" });
  act(() => { button.click(); button.click(); });
  await waitFor(() => expect(update).toHaveBeenCalledOnce());
  expect(update).toHaveBeenCalledWith(draft.id, { material_name: "NEW DRAFT" }, expect.any(String));
  expect(requestNavigation("/materials")).toBe(false);
  await act(async () => finish({ ...draft, materialName: "NEW-DRAFT" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(changed).toHaveBeenCalledOnce(); expect(identityClient.plan).not.toHaveBeenCalled(); expect(identityClient.confirm).not.toHaveBeenCalled();
  await waitFor(() => expect(requestNavigation("/materials")).toBe(true));
});

it("retains an unknown name request through remount and a later 403, then retries its exact packet", async () => {
  const update = vi.fn().mockRejectedValueOnce(new TypeError("response lost"))
    .mockRejectedValueOnce(new ApiError(403, "Access temporarily unavailable"))
    .mockResolvedValue({ ...draft, materialName: "NEW-DRAFT" });
  const first = mount(update); submit();
  expect(await screen.findByRole("alert")).toHaveTextContent("outcome is unknown");
  first.unmount(); const recovered = mount(update);
  expect(screen.getByLabelText("Material name")).toHaveValue("NEW DRAFT");
  fireEvent.click(screen.getByRole("button", { name: "Retry exact save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("outcome is unknown");
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(screen.getByLabelText("Material name")).toBeDisabled(); expect(requestNavigation("/materials")).toBe(false);
  fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
  expect(recovered.close).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Retry exact save" }));
  await waitFor(() => expect(recovered.close).toHaveBeenCalledOnce());
  expect(update).toHaveBeenCalledTimes(3);
  expect(update.mock.calls[1]).toEqual(update.mock.calls[0]); expect(update.mock.calls[2]).toEqual(update.mock.calls[0]);
  expect(first.changed).not.toHaveBeenCalled(); expect(first.close).not.toHaveBeenCalled();
  expect(recovered.changed).toHaveBeenCalledOnce();
});

it("does not apply a late save callback after the authenticated session changes", async () => {
  let finish!: (material: Material) => void;
  const update = vi.fn().mockImplementation(() => new Promise<Material>(resolve => { finish = resolve; }));
  const { changed, close } = mount(update); submit();
  await waitFor(() => expect(update).toHaveBeenCalledOnce());
  setSessionToken("new-session");
  await act(async () => finish({ ...draft, materialName: "NEW-DRAFT" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("outcome is unknown");
  expect(changed).not.toHaveBeenCalled(); expect(close).not.toHaveBeenCalled();
  expect(pendingRecordCommands.get(processorDto.id)?.wasUnknown).toBe(true);
});

it("allows closing the dialog to recover a different pending record save", () => {
  pendingRecordCommands.set(processorDto.id, { key: processorDto.id, requestHash: "a".repeat(64),
    scope: { kind: "MATERIAL", action: "UPDATED", targetId: draft.id, editorPath: `/materials/${draft.id}/edit` },
    values: { name: "OTHER" }, save: vi.fn(), phase: "UNKNOWN", wasUnknown: true });
  const update = vi.fn(), { close } = mount(update);
  expect(screen.getByRole("alert")).toHaveTextContent("other pending record save");
  expect(screen.getByLabelText("Material name")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(close).toHaveBeenCalledOnce(); expect(update).not.toHaveBeenCalled();
  expect(requestNavigation(`/materials/${draft.id}/edit`)).toBe(true);
});
