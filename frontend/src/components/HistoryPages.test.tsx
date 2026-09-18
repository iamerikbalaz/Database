import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../api/errors";
import { HistoryPages } from "./HistoryPages";

const actor = vi.hoisted(() => ({ id: "first-actor" }));
vi.mock("../auth/context", () => ({ useSession: () => ({ session: { user: { id: actor.id } } }) }));
const initial = Array.from({ length: 100 }, (_, index) => ({ id: `record-${100 - index}` }));
const renderItems = (items: { id: string }[]) => <ul>{items.map((item) => <li key={item.id}>{item.id}</li>)}</ul>;
beforeEach(() => { actor.id = "first-actor"; });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

it("loads bounded older pages, returns to latest and never initiates automatic reads", async () => {
  const load = vi.fn().mockResolvedValueOnce([{ id: "older-record" }]).mockResolvedValueOnce(initial);
  render(<HistoryPages scope="material" label="test history" initial={initial} load={load}>{renderItems}</HistoryPages>);
  expect(load).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Older test history" }));
  await screen.findByText("older-record");
  expect(load).toHaveBeenCalledWith("record-1");
  expect(screen.queryByText("record-100")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Older test history" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Latest test history" }));
  await screen.findByText("record-100");
  expect(load).toHaveBeenLastCalledWith(null);
});

it("does not assume a full page proves another page exists", async () => {
  const load = vi.fn().mockResolvedValue([]);
  render(<HistoryPages scope="material" label="test history" initial={initial} load={load}>{renderItems}</HistoryPages>);
  fireEvent.click(screen.getByRole("button", { name: "Older test history" }));
  await screen.findByText("No older records.");
  expect(screen.queryByRole("button", { name: "Older test history" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Latest test history" })).toBeEnabled();
});

it("blocks duplicate reads and preserves the page after a transient failure", async () => {
  const task = deferred<{ id: string }[]>();
  const load = vi.fn().mockReturnValueOnce(task.promise).mockRejectedValueOnce(new Error("connection unavailable"));
  render(<HistoryPages scope="material" label="test history" initial={initial} load={load}>{renderItems}</HistoryPages>);
  const older = screen.getByRole("button", { name: "Older test history" });
  fireEvent.click(older); fireEvent.click(older);
  expect(load).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "Latest test history" })).toBeDisabled();
  await act(async () => task.resolve(initial));
  fireEvent.click(screen.getByRole("button", { name: "Older test history" }));
  await screen.findByRole("alert");
  expect(screen.getByText("record-100")).toBeInTheDocument();
  expect(older).toBeEnabled();
});

it.each([401, 403, 404])("hides retained history after authorization failure %s", async (status) => {
  const load = vi.fn().mockRejectedValue(new ApiError(status, "Unavailable"));
  render(<HistoryPages scope="material" label="test history" initial={initial} load={load}>{renderItems}</HistoryPages>);
  fireEvent.click(screen.getByRole("button", { name: "Older test history" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("record-100")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Load test history" })).toBeEnabled();
});

it.each(["actor", "target", "latest"])("discards late reads after the %s changes", async (kind) => {
  const task = deferred<{ id: string }[]>();
  const load = vi.fn().mockReturnValue(task.promise);
  const tree = (scope: string, rows = initial) => <HistoryPages scope={scope} label="test history" initial={rows} load={load}>{renderItems}</HistoryPages>;
  const view = render(tree("first-material"));
  fireEvent.click(screen.getByRole("button", { name: "Older test history" }));
  if (kind === "actor") actor.id = "second-actor";
  view.rerender(tree(kind === "target" ? "second-material" : "first-material", kind === "latest" ? [{ id: "fresh-record" }] : initial));
  await act(async () => task.resolve([{ id: "late-record" }]));
  expect(screen.queryByText("late-record")).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Latest test history" })).toBeEnabled());
});
