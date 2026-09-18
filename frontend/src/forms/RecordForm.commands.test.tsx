import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SessionContext } from "../auth/context";
import { ApiError } from "../api/errors";
import { resourceCommandClient } from "../api/resourceCommandClient";
import { processorDto } from "../test/materialFixtures";
import { RecordForm, type FormDefinition } from "./RecordForm";
import { pendingRecordCommands } from "./pendingRecordCommands";

const owners: string[] = [];
function owner() { const id = crypto.randomUUID(); owners.push(id); return id; }
afterEach(() => { for (const id of owners.splice(0)) pendingRecordCommands.set(id, null); vi.restoreAllMocks(); });
const result = { path: "/companies/10000000-0000-4000-8000-000000000001", message: "Saved" };
function definition(save: FormDefinition["save"]): FormDefinition {
  return { title: "Create company", fields: [{ name: "name", apiName: "name", label: "Name", required: true }],
    initial: { name: "Original submitted name" }, cancel: "/companies", save,
    command: { kind: "COMPANY", action: "CREATED", targetId: null, editorPath: "/companies/new", payload: (values) => ({ name: values.name }) } };
}
function tree(actor: string, value: FormDefinition, saved = vi.fn(), navigate = vi.fn()) {
  return <SessionContext.Provider value={{ session: { user: { ...processorDto, id: actor, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <RecordForm definition={value} navigate={navigate} onSaved={saved} />
  </SessionContext.Provider>;
}
async function makeUnknown() {
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByRole("button", { name: "Retry exact save" });
}

it("freezes unknown values and retries the same key and original payload", async () => {
  const save = vi.fn().mockRejectedValueOnce(new Error("connection unavailable")).mockResolvedValueOnce(result), saved = vi.fn();
  render(tree(owner(), definition(save), saved)); await makeUnknown();
  expect(screen.getByLabelText("Name *")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  const first = save.mock.calls[0];
  expect(first[1]).toMatch(/^[a-f0-9-]{36}$/);
  fireEvent.change(screen.getByLabelText("Name *"), { target: { value: "Attempted replacement" } });
  fireEvent.click(screen.getByRole("button", { name: "Retry exact save" }));
  await waitFor(() => expect(saved).toHaveBeenCalledWith(result.path, result.message));
  expect(save.mock.calls[1]).toEqual(first);
  expect(save.mock.calls[1][0]).toEqual({ name: "Original submitted name" });
});

it("reads a saved receipt explicitly without resending and warns before reload until resolved", async () => {
  const actor = owner(), save = vi.fn().mockRejectedValue(new Error("connection unavailable")), saved = vi.fn();
  const recover = vi.spyOn(resourceCommandClient, "recover").mockResolvedValue({ ...result, id: actor, resourceId: actor, createdAt: "2026-09-18T08:00:00Z", requestHash: "a".repeat(64), responseHash: "b".repeat(64) });
  render(tree(actor, definition(save), saved)); await makeUnknown();
  expect(recover).not.toHaveBeenCalled();
  const warned = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(warned); expect(warned.defaultPrevented).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Check saved result" }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(recover).toHaveBeenCalledWith(expect.objectContaining({ kind: "COMPANY", action: "CREATED", targetId: null }), actor, save.mock.calls[0][1], expect.stringMatching(/^[a-f0-9]{64}$/));
  expect(save).toHaveBeenCalledOnce();
  const resolved = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(resolved); expect(resolved.defaultPrevented).toBe(false);
});

it("keeps a missing receipt and a later explicit denial uncertain", async () => {
  const save = vi.fn().mockRejectedValueOnce(new Error("connection unavailable")).mockRejectedValueOnce(new ApiError(403, "Unavailable"));
  vi.spyOn(resourceCommandClient, "recover").mockRejectedValue(new ApiError(404, "Unavailable"));
  render(tree(owner(), definition(save))); await makeUnknown();
  fireEvent.click(screen.getByRole("button", { name: "Check saved result" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Retry exact save" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Retry exact save" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(save.mock.calls[1][1]).toBe(save.mock.calls[0][1]);
});

it("allows correction with a fresh key after a definite first rejection", async () => {
  const save = vi.fn().mockRejectedValueOnce(new ApiError(422, "Review the name", [{ field: "name", message: "Choose a name" }])).mockResolvedValueOnce(result), saved = vi.fn();
  render(tree(owner(), definition(save), saved));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Name *")).toBeEnabled();
  fireEvent.change(screen.getByLabelText("Name *"), { target: { value: "Corrected name" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(save.mock.calls[1][1]).not.toBe(save.mock.calls[0][1]);
});

it("retains an unresolved form across navigation and blocks another form for that actor", async () => {
  const actor = owner(), save = vi.fn().mockRejectedValue(new Error("connection unavailable")), navigate = vi.fn();
  const view = render(tree(actor, definition(save))); await makeUnknown(); view.unmount();
  const other = { ...definition(vi.fn()), title: "Another form", command: { ...definition(save).command!, editorPath: "/companies/another/edit" } };
  const next = render(tree(actor, other, vi.fn(), navigate));
  fireEvent.click(screen.getByRole("button", { name: "Open pending form" }));
  expect(navigate).toHaveBeenCalledWith("/companies/new");
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  next.unmount(); render(tree(actor, definition(save)));
  expect(screen.getByLabelText("Name *")).toHaveValue("Original submitted name");
  expect(screen.getByRole("button", { name: "Retry exact save" })).toBeEnabled();
  expect(save).toHaveBeenCalledOnce();
});

it("keeps actors separate while retaining each actor's unresolved request", async () => {
  const first = owner(), second = owner(), save = vi.fn().mockRejectedValue(new Error("connection unavailable"));
  const view = render(tree(first, definition(save))); await makeUnknown();
  const firstKey = save.mock.calls[0][1];
  view.rerender(tree(second, definition(save)));
  expect(screen.queryByText(firstKey, { exact: false })).not.toBeInTheDocument();
  await makeUnknown(); expect(save.mock.calls[1][1]).not.toBe(firstKey);
  view.rerender(tree(first, definition(save)));
  expect(screen.getByText(firstKey, { exact: false })).toBeInTheDocument();
  expect(save).toHaveBeenCalledTimes(2);
});

it("records a late successful save after navigation without navigating the new page", async () => {
  let resolve!: (value: typeof result) => void;
  const save = vi.fn(() => new Promise<typeof result>((done) => { resolve = done; }));
  const actor = owner(), saved = vi.fn(); const view = render(tree(actor, definition(save), saved));
  fireEvent.click(screen.getByRole("button", { name: "Save" })); await waitFor(() => expect(save).toHaveBeenCalledOnce()); view.unmount();
  await act(async () => resolve(result)); expect(saved).not.toHaveBeenCalled();
  const recovered = vi.fn(); render(tree(actor, definition(save), recovered));
  fireEvent.click(screen.getByRole("button", { name: "Open saved record" }));
  expect(recovered).toHaveBeenCalledWith(result.path, result.message);
  expect(save).toHaveBeenCalledOnce();
});

it("never dispatches a save when its form unmounts during digest preparation", async () => {
  let complete!: (value: ArrayBuffer) => void;
  vi.spyOn(crypto.subtle, "digest").mockImplementationOnce(() => new Promise<ArrayBuffer>((resolve) => { complete = resolve; }));
  const id = owner(), save = vi.fn().mockResolvedValue(result), view = render(tree(id, definition(save)));
  fireEvent.click(screen.getByRole("button", { name: "Save" })); view.unmount();
  await act(async () => complete(new ArrayBuffer(32)));
  expect(save).not.toHaveBeenCalled(); expect(pendingRecordCommands.get(id)).toBeNull();
});
