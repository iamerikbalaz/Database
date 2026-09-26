import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { EditableResourceTable, type ResourceColumn } from "./EditableResourceTable";
import { setSessionToken } from "../auth/sessionTransport";
import { ApiError } from "../api/errors";

type Row = { id: string; name: string; version: number; status: string; note: string | null };
const rows: Row[] = [{ id: "one", name: "First", version: 1, status: "OPEN", note: null }, { id: "two", name: "Second", version: 2, status: "OPEN", note: null }];
const columns: ResourceColumn<Row>[] = [{ key: "name", label: "Name", value: row => row.name },
  { key: "status", label: "Status", value: row => row.status, editable: true, bulk: true, options: [{ value: "OPEN", label: "Open" }, { value: "DONE", label: "Done" }] },
  { key: "note", label: "Note", value: row => row.note, type: "textarea", editable: true, bulk: true }];
beforeEach(() => { localStorage.clear(); HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); }; HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); }; });
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });
function setup(save = vi.fn(async (row: Row, field: string, value: string | boolean | null) => ({ ...row, [field]: value, version: row.version + 1 }))) {
  return { save, ...render(<EditableResourceTable rows={rows} columns={columns} label={row => row.name} save={save} canEdit refresh={vi.fn()} storageKey="test.columns" />) };
}
function bulk() { fireEvent.change(screen.getByRole("combobox", { name: "Bulk value" }), { target: { value: "DONE" } }); fireEvent.click(screen.getByRole("button", { name: "Apply to all 2 filtered" })); return screen.getByRole("dialog"); }
it("uses the original row version, applies the saved row and preserves an unsaved note", async () => {
  const { save } = setup(); fireEvent.change(screen.getByRole("textbox", { name: "Note for First" }), { target: { value: "Draft #tag" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Status for First" }), { target: { value: "DONE" } });
  await screen.findByText("Saved. Refresh to reapply filters.");
  expect(save).toHaveBeenCalledWith(rows[0], "status", "DONE", expect.any(String));
  expect(screen.getByRole("combobox", { name: "Status for First" })).toHaveValue("DONE");
  expect(screen.getByRole("textbox", { name: "Note for First" })).toHaveValue("Draft #tag");
});
it("confirms a frozen batch and reports per-row conflicts without retrying successes", async () => {
  const save = vi.fn().mockResolvedValueOnce({ ...rows[0], status: "DONE", version: 3 }).mockRejectedValueOnce(new ApiError(409, "Changed")); setup(save);
  const dialog = bulk(); expect(save).not.toHaveBeenCalled(); fireEvent.click(within(dialog).getByRole("button", { name: "Confirm changes" }));
  await within(dialog).findByText(/Record changed/); expect(save.mock.calls.map(call => call[0].id)).toEqual(["one", "two"]); expect(new Set(save.mock.calls.map(call => call[3])).size).toBe(2);
  expect(within(dialog).queryByRole("button", { name: "Confirm changes" })).not.toBeInTheDocument();
});
it("pauses uncertain writes, retries the same packet and does not mistake later403 for rejection", async () => {
  const save = vi.fn().mockRejectedValueOnce(new TypeError("Lost reply")).mockRejectedValueOnce(new ApiError(403, "Changed access"))
    .mockResolvedValueOnce({ ...rows[0], status: "DONE" }).mockResolvedValueOnce({ ...rows[1], status: "DONE" }); setup(save);
  const dialog = bulk(); fireEvent.click(within(dialog).getByRole("button", { name: "Confirm changes" }));
  fireEvent.click(await within(dialog).findByRole("button", { name: "Retry same request and continue" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2)); await waitFor(() => expect(within(dialog).getByRole("button", { name: "Retry same request and continue" })).toBeEnabled());
  expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled(); expect(save.mock.calls[1]).toEqual(save.mock.calls[0]);
  fireEvent.click(within(dialog).getByRole("button", { name: "Retry same request and continue" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(4)); expect(save.mock.calls[2]).toEqual(save.mock.calls[0]); expect(save.mock.calls[3][0].id).toBe("two");
});
it("stops safely after the in-flight row", async () => {
  let finish!: (row: Row) => void; const save = vi.fn().mockReturnValue(new Promise<Row>(resolve => { finish = resolve; })); setup(save);
  const dialog = bulk(); fireEvent.click(within(dialog).getByRole("button", { name: "Confirm changes" })); fireEvent.click(within(dialog).getByRole("button", { name: "Stop after current record" }));
  await act(async () => finish({ ...rows[0], status: "DONE" })); expect(save).toHaveBeenCalledTimes(1); expect(within(dialog).getByText(/Not attempted/)).toBeVisible();
});
it("clears a note to null and persists property visibility", async () => {
  const save = vi.fn(async (row: Row) => ({ ...row, note: null })); render(<EditableResourceTable rows={[{ ...rows[0], note: "old" }]} columns={columns} label={row => row.name} save={save} canEdit refresh={vi.fn()} storageKey="test.columns" />);
  fireEvent.change(screen.getByRole("textbox", { name: "Note for First" }), { target: { value: "" } }); fireEvent.click(screen.getByRole("button", { name: "Save note" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ note: "old" }), "note", null, expect.any(String)));
  fireEvent.click(screen.getByText("Properties")); fireEvent.click(screen.getByRole("checkbox", { name: "Note" })); expect(screen.queryByRole("columnheader", { name: "Note" })).not.toBeInTheDocument();
  expect(JSON.parse(localStorage.getItem("test.columns")!)).toContain("note");
});

it("stops untouched rows when the session changes before confirmation", async () => {
  const { save } = setup(); const dialog = bulk(); setSessionToken("changed-session");
  fireEvent.click(within(dialog).getByRole("button", { name: "Confirm changes" }));
  await waitFor(() => expect(within(dialog).queryByRole("button", { name: "Confirm changes" })).not.toBeInTheDocument());
  expect(save).not.toHaveBeenCalled(); expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
  expect(within(dialog).getAllByText(/Session changed/)).toHaveLength(2);
});

it("does not continue the queue after an in-flight response changes the session", async () => {
  let finish!: (row: Row) => void; const save = vi.fn().mockReturnValue(new Promise<Row>(resolve => { finish = resolve; })); setup(save);
  const dialog = bulk(); fireEvent.click(within(dialog).getByRole("button", { name: "Confirm changes" })); setSessionToken("new-session");
  await act(async () => finish({ ...rows[0], status: "DONE" }));
  expect(save).toHaveBeenCalledTimes(1); expect(within(dialog).getByText(/Saved before session changed/)).toBeVisible();
  expect(within(dialog).getByText(/Session changed. Reload/)).toBeVisible(); expect(within(dialog).queryByRole("button", { name: "Confirm changes" })).not.toBeInTheDocument();
});
