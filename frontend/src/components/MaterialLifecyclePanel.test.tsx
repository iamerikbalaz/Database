import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialLifecyclePanel } from "./MaterialLifecyclePanel";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";
import { materialArchiveClient as api, archivePreview, lifecycleEvent, type LifecycleEvent } from "../api/materialArchiveClient";
import { ApiError } from "../api/errors";
import { archiveMaterialId, archivePreviewDto, lifecycleEventDto } from "../test/materialArchiveFixtures";

let actor: string;
beforeEach(() => {
  actor = crypto.randomUUID();
  vi.spyOn(api, "history").mockResolvedValue({ items: [], nextCursor: null });
  vi.spyOn(api, "preview").mockImplementation(async (id, action) => archivePreview(archivePreviewDto(action === "RESTORE" ? 1 : 0, id), id, action));
  vi.spyOn(api, "command").mockImplementation(async (preview, actorId, body) => lifecycleEvent({ ...lifecycleEventDto(body.expected_version + 1, preview.id),
    actor_id: actorId, request_key: body.request_key, reason: body.reason }, preview.id));
  vi.spyOn(api, "recover").mockImplementation(async (preview, actorId, body) => lifecycleEvent({ ...lifecycleEventDto(body.expected_version + 1, preview.id),
    actor_id: actorId, request_key: body.request_key, reason: body.reason }, preview.id));
});
afterEach(() => vi.restoreAllMocks());
function mount(role: Role = "ADMIN", archived = false) {
  const applied = vi.fn(), navigate = vi.fn();
  const view = (id = archiveMaterialId, currentActor = actor, currentRole = role) => <SessionContext.Provider value={{
    session: { user: { id: currentActor, role: currentRole, display_name: "Synthetic administrator", email: "operator@example.invalid" },
      must_change_password: false, csrf_token: "" }, pending: false, logout: vi.fn(), changePassword: vi.fn(),
  }}><MaterialLifecyclePanel materialId={id} archived={archived} navigate={navigate} onApplied={applied} /></SessionContext.Provider>;
  const rendered = render(view());
  return { ...rendered, applied, navigate, change: (id: string, idActor = actor, nextRole = role) => rendered.rerender(view(id, idActor, nextRole)) };
}

async function toggle() { fireEvent.click(screen.getByRole("checkbox", { name: "Archived" })); await waitFor(() => expect(api.preview).toHaveBeenCalled()); }

it.each([false, true])("toggles Archived without reason or extra acknowledgment, restore=%s", async (restore) => {
  const view = mount("ADMIN", restore); await toggle();
  await waitFor(() => expect(view.applied).toHaveBeenCalledTimes(1));
  expect(api.preview).toHaveBeenCalledWith(archiveMaterialId, restore ? "RESTORE" : "ARCHIVE");
  expect(vi.mocked(api.command).mock.calls[0][2]).toMatchObject({ action: restore ? "RESTORE" : "ARCHIVE", acknowledge: true, reason: "Archived property changed." });
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  expect(api.history).not.toHaveBeenCalled();
});
it.each(["PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP"] as const)("keeps the archive property read only for %s", role => {
  mount(role); expect(screen.getByRole("checkbox", { name: "Archived" })).toBeDisabled(); expect(api.preview).not.toHaveBeenCalled();
});
it("shows work and external state blockers without issuing a command", async () => {
  vi.mocked(api.preview).mockResolvedValueOnce({ ...archivePreview(archivePreviewDto(), archiveMaterialId, "ARCHIVE"), canApply: false, blockedCode: "MATERIAL_LIFECYCLE_EXTERNAL_STATE_BLOCKED" });
  mount(); await toggle(); await screen.findByText(/Closing that job does not prove/);
  expect(api.command).not.toHaveBeenCalled(); expect(screen.getByRole("checkbox")).not.toBeChecked();
});
it("retains an uncertain request through remount, missing recovery and exact retry", async () => {
  vi.mocked(api.command).mockRejectedValueOnce(new TypeError("Synthetic response loss"));
  vi.mocked(api.recover).mockRejectedValueOnce(new ApiError(404, "Missing"));
  const storage = vi.spyOn(Storage.prototype, "setItem"); const view = mount(); await toggle();
  await screen.findByText(/The outcome could not be verified/);
  const original = vi.mocked(api.command).mock.calls[0][2];
  const leaving = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(leaving); expect(leaving.defaultPrevented).toBe(true);
  view.unmount(); mount(); expect(api.command).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Check saved lifecycle result" }));
  await screen.findByText(/The original request may still commit/);
  fireEvent.click(screen.getByRole("button", { name: "Retry exact lifecycle request" }));
  await waitFor(() => expect(screen.getByRole("checkbox")).toBeChecked());
  expect(vi.mocked(api.command).mock.calls[1][2]).toEqual(original); expect(storage).not.toHaveBeenCalled();
});
it("recovers by reading without resending the command", async () => {
  vi.mocked(api.command).mockRejectedValueOnce(new ApiError(503, "Unavailable"));
  mount(); await toggle(); await screen.findByText(/The outcome could not be verified/);
  fireEvent.click(screen.getByRole("button", { name: "Check saved lifecycle result" }));
  await waitFor(() => expect(screen.getByRole("checkbox")).toBeChecked());
  expect(api.command).toHaveBeenCalledTimes(1); expect(api.recover).toHaveBeenCalledTimes(1);
});
it("releases a rejected first command but keeps a later uncertain denial", async () => {
  vi.mocked(api.command).mockRejectedValueOnce(new ApiError(409, "Changed"));
  mount(); await toggle(); await screen.findByText(/The action was rejected/);
  vi.mocked(api.command).mockRejectedValueOnce(new TypeError("Synthetic loss")); await toggle();
  await screen.findByText(/The outcome could not be verified/);
  vi.mocked(api.command).mockRejectedValueOnce(new ApiError(403, "Denied"));
  fireEvent.click(screen.getByRole("button", { name: "Retry exact lifecycle request" }));
  await waitFor(() => expect(api.command).toHaveBeenCalledTimes(3));
  expect(await screen.findByRole("button", { name: "Check saved lifecycle result" })).toBeVisible();
  expect(screen.getByRole("checkbox")).toBeDisabled();
});
it("keeps an uncertain packet scoped to its actor and target", async () => {
  vi.mocked(api.command).mockRejectedValueOnce(new TypeError("Synthetic loss"));
  const view = mount(); await toggle(); await screen.findByText(/The outcome could not be verified/);
  view.change(crypto.randomUUID()); fireEvent.click(screen.getByRole("button", { name: "Open pending material" }));
  expect(view.navigate).toHaveBeenCalledWith(`/material-archives/${archiveMaterialId}`);
  expect(screen.getByRole("checkbox")).toBeDisabled();
  view.change(archiveMaterialId, crypto.randomUUID()); expect(screen.getByRole("checkbox")).toBeEnabled();
  expect(api.command).toHaveBeenCalledTimes(1);
});
it("does not send after a preview resolves beyond unmount", async () => {
  let finish!: (value: Awaited<ReturnType<typeof api.preview>>) => void;
  vi.mocked(api.preview).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  const view = mount(); await toggle(); view.unmount();
  await act(async () => finish(archivePreview(archivePreviewDto(), archiveMaterialId, "ARCHIVE")));
  expect(api.command).not.toHaveBeenCalled();
});
it("discards late success after the signed-in actor changes", async () => {
  let finish!: (value: LifecycleEvent) => void;
  vi.mocked(api.command).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const view = mount(); await toggle(); await waitFor(() => expect(api.command).toHaveBeenCalledTimes(1));
  view.change(archiveMaterialId, crypto.randomUUID());
  await act(async () => finish(lifecycleEvent(lifecycleEventDto(), archiveMaterialId)));
  expect(view.applied).not.toHaveBeenCalled(); expect(screen.getByRole("checkbox")).not.toBeChecked();
});
