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
function open() {
  const details = screen.getByText("Archive and restore", { selector: "summary" }).parentElement as HTMLDetailsElement;
  details.open = true; fireEvent(details, new Event("toggle")); return details;
}
async function review(restore = false) {
  fireEvent.click(await screen.findByRole("button", { name: restore ? "Review restore" : "Review archive" }));
  await screen.findByLabelText("Reason for lifecycle change");
}
function confirm(restore = false) {
  fireEvent.change(screen.getByLabelText("Reason for lifecycle change"), { target: { value: "Reviewed lifecycle decision" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: restore ? "Confirm restore" : "Confirm archive" }));
}

it.each([false, true])("requires an explicit review, reason and acknowledgment for restore=%s", async (restore) => {
  const view = mount("ADMIN", restore); expect(api.history).not.toHaveBeenCalled(); open(); await review(restore);
  expect(api.preview).toHaveBeenCalledWith(archiveMaterialId, restore ? "RESTORE" : "ARCHIVE");
  expect(screen.getByRole("button", { name: restore ? "Confirm restore" : "Confirm archive" })).toBeDisabled();
  confirm(restore);
  await screen.findByRole("status"); await waitFor(() => expect(view.applied).toHaveBeenCalledTimes(1));
  expect(api.command).toHaveBeenCalledTimes(1);
  expect(vi.mocked(api.command).mock.calls[0][2]).toMatchObject({ action: restore ? "RESTORE" : "ARCHIVE", expected_version: restore ? 1 : 0, acknowledge: true });
});
it.each(["PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP"] as const)("hides all lifecycle controls from %s", (role) => {
  mount(role); expect(screen.queryByText("Archive and restore")).not.toBeInTheDocument(); expect(api.history).not.toHaveBeenCalled();
});
it("shows eligibility blockers without exposing a confirm action", async () => {
  vi.mocked(api.preview).mockResolvedValueOnce({ ...archivePreview(archivePreviewDto(), archiveMaterialId, "ARCHIVE"), canApply: false, blockedCode: "MATERIAL_OPERATION_ACTIVE" });
  mount(); open(); fireEvent.click(await screen.findByRole("button", { name: "Review archive" }));
  await screen.findByText(/This action is currently blocked/);
  expect(screen.queryByRole("button", { name: "Confirm archive" })).not.toBeInTheDocument(); expect(api.command).not.toHaveBeenCalled();
});
it("explains that closing a storage job does not reconcile external files", async () => {
  vi.mocked(api.preview).mockResolvedValueOnce({ ...archivePreview(archivePreviewDto(), archiveMaterialId, "ARCHIVE"), canApply: false, blockedCode: "MATERIAL_LIFECYCLE_EXTERNAL_STATE_BLOCKED" });
  mount(); open(); fireEvent.click(await screen.findByRole("button", { name: "Review archive" }));
  await screen.findByText(/Closing that job does not prove that external files were removed/);
  expect(screen.queryByRole("button", { name: "Confirm archive" })).not.toBeInTheDocument(); expect(api.command).not.toHaveBeenCalled();
});
it("keeps an uncertain exact packet through collapse and missing read recovery", async () => {
  vi.mocked(api.command).mockRejectedValueOnce(new TypeError("Synthetic response loss"));
  vi.mocked(api.recover).mockRejectedValueOnce(new ApiError(404, "Missing"));
  const storage = vi.spyOn(Storage.prototype, "setItem"); mount(); const details = open(); await review(); confirm();
  await screen.findByText(/The outcome could not be verified/);
  const original = vi.mocked(api.command).mock.calls[0][2];
  const leaving = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(leaving); expect(leaving.defaultPrevented).toBe(true);
  details.open = false; fireEvent(details, new Event("toggle")); open();
  expect(api.command).toHaveBeenCalledTimes(1);
  fireEvent.click(await screen.findByRole("button", { name: "Check saved lifecycle result" }));
  await screen.findByText(/The original request may still commit/);
  expect(screen.queryByRole("button", { name: "Review archive" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry exact lifecycle request" }));
  await screen.findByText(/Recorded archive action/);
  expect(vi.mocked(api.command).mock.calls[1][2]).toEqual(original); expect(api.recover).toHaveBeenCalledTimes(1);
  expect(storage).not.toHaveBeenCalled();
});
it("recovers by reading without resending the command", async () => {
  vi.mocked(api.command).mockRejectedValueOnce(new ApiError(503, "Unavailable"));
  mount(); open(); await review(); confirm(); await screen.findByText(/The outcome could not be verified/);
  fireEvent.click(screen.getByRole("button", { name: "Check saved lifecycle result" }));
  await screen.findByText(/Recorded archive action/); expect(api.command).toHaveBeenCalledTimes(1); expect(api.recover).toHaveBeenCalledTimes(1);
});
it("releases an explicitly rejected first command but keeps an uncertain later denial", async () => {
  vi.mocked(api.command).mockRejectedValueOnce(new ApiError(409, "Changed"));
  mount(); open(); await review(); confirm(); await screen.findByText(/The action was rejected/);
  await review(); vi.mocked(api.command).mockRejectedValueOnce(new TypeError("Synthetic response loss")); confirm();
  await screen.findByText(/The outcome could not be verified/);
  vi.mocked(api.command).mockRejectedValueOnce(new ApiError(403, "Denied"));
  fireEvent.click(screen.getByRole("button", { name: "Retry exact lifecycle request" }));
  await waitFor(() => expect(api.command).toHaveBeenCalledTimes(3));
  await screen.findByRole("button", { name: "Check saved lifecycle result" });
  expect(screen.queryByRole("button", { name: "Review archive" })).not.toBeInTheDocument();
});
it("keeps a pending material scoped across in-app navigation", async () => {
  vi.mocked(api.command).mockRejectedValueOnce(new TypeError("Synthetic response loss"));
  const view = mount(); open(); await review(); confirm(); await screen.findByText(/The outcome could not be verified/);
  view.change(crypto.randomUUID());
  fireEvent.click(await screen.findByRole("button", { name: "Open pending material" }));
  expect(view.navigate).toHaveBeenCalledWith(`/material-archives/${archiveMaterialId}`);
  expect(screen.queryByRole("button", { name: "Review archive" })).not.toBeInTheDocument(); expect(api.command).toHaveBeenCalledTimes(1);
});
it("discards late success after the signed-in actor changes", async () => {
  let finish: (value: LifecycleEvent) => void = () => undefined;
  vi.mocked(api.command).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const view = mount(); open(); await review(); confirm();
  await waitFor(() => expect(api.command).toHaveBeenCalledTimes(1));
  const original = vi.mocked(api.command).mock.calls[0]; view.change(archiveMaterialId, crypto.randomUUID());
  await act(async () => finish(lifecycleEvent({ ...lifecycleEventDto(), actor_id: actor, request_key: original[2].request_key }, archiveMaterialId)));
  expect(view.applied).not.toHaveBeenCalled(); expect(screen.queryByText(/Recorded archive action/)).not.toBeInTheDocument();
});
it("keeps separate uncertain packets when two actors use the same browser", async () => {
  vi.mocked(api.command).mockRejectedValue(new TypeError("Synthetic response loss"));
  const view = mount(); const details = open(); await review(); confirm(); await screen.findByText(/The outcome could not be verified/);
  const first = vi.mocked(api.command).mock.calls[0][2];
  details.open = false; fireEvent(details, new Event("toggle"));
  const leaving = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(leaving); expect(leaving.defaultPrevented).toBe(true);
  open(); view.change(archiveMaterialId, crypto.randomUUID()); await review(); confirm(); await screen.findByText(/The outcome could not be verified/);
  expect(vi.mocked(api.command).mock.calls[1][2].request_key).not.toBe(first.request_key);
  view.change(archiveMaterialId, actor);
  fireEvent.click(await screen.findByRole("button", { name: "Check saved lifecycle result" }));
  await screen.findByText(/Recorded archive action/);
  expect(vi.mocked(api.recover).mock.calls[0][1]).toBe(actor);
  expect(vi.mocked(api.recover).mock.calls[0][2]).toEqual(first);
  expect(api.command).toHaveBeenCalledTimes(2);
});
it("reads older history without sending mutations", async () => {
  const first = lifecycleEvent(lifecycleEventDto(2), archiveMaterialId), older = lifecycleEvent(lifecycleEventDto(1), archiveMaterialId);
  vi.mocked(api.history).mockResolvedValueOnce({ items: [first], nextCursor: first.id }).mockResolvedValueOnce({ items: [older], nextCursor: null });
  mount(); open(); fireEvent.click(await screen.findByRole("button", { name: "Older lifecycle actions" }));
  await screen.findByText(/^Archived ·/); expect(api.history).toHaveBeenLastCalledWith(archiveMaterialId, first.id);
  expect(api.command).not.toHaveBeenCalled();
});
