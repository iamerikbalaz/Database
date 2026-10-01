import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { SessionContext } from "../auth/context";
import { processorDto } from "../test/materialFixtures";
import { sortDatabaseRecords, useDatabaseFilters } from "./useDatabaseFilters";

beforeEach(() => localStorage.clear());
function Demo({ scope = "materials" }: { scope?: string }) {
  const { filters, setFilters, keepFilters, setKeepFilters, resetFilters } = useDatabaseFilters(scope, { search: "", colors: [] as string[], sort: "created-desc" });
  return <><label>Search<input value={filters.search} onChange={event => setFilters(current => ({ ...current, search: event.target.value }))} /></label>
    <label>Keep filters<input type="checkbox" checked={keepFilters} onChange={event => setKeepFilters(event.target.checked)} /></label><button onClick={resetFilters}>Clear filters</button></>;
}
function user(id: string, scope?: string) {
  return <SessionContext.Provider value={{ session: { user: { ...processorDto, id }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}><Demo scope={scope} /></SessionContext.Provider>;
}
it("remembers filters only when opted in and isolates users and database views", () => {
  const view = render(user("first"));
  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "Marble" } });
  fireEvent.click(screen.getByLabelText("Keep filters"));
  view.rerender(user("second")); expect(screen.getByLabelText("Search")).toHaveValue("");
  expect(screen.getByLabelText("Keep filters")).not.toBeChecked();
  view.rerender(user("first", "archive")); expect(screen.getByLabelText("Search")).toHaveValue("");
  view.rerender(user("first")); expect(screen.getByLabelText("Search")).toHaveValue("Marble");
  expect(screen.getByLabelText("Keep filters")).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(screen.getByLabelText("Keep filters")).toBeChecked();
  expect(screen.getByLabelText("Search")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "Unsaved" } });
  fireEvent.click(screen.getByLabelText("Keep filters"));
  view.unmount(); render(user("first"));
  expect(screen.getByLabelText("Search")).toHaveValue("");
});
it("ignores malformed and unexpected persisted field types", () => {
  localStorage.setItem("reawote-filters:first:materials:v1", JSON.stringify({ keep: true, filters: { search: { invalid: true }, colors: [42], sort: "name-asc", extra: "ignored" } }));
  render(user("first")); expect(screen.getByLabelText("Search")).toHaveValue("");
  expect(JSON.parse(localStorage.getItem("reawote-filters:first:materials:v1")!).filters).toEqual({ search: "", colors: [], sort: "name-asc" });
});
it("sorts all four directions without mutating records, placing missing dates last", () => {
  const records = [{ id: "c", name: "Stone 10", date: null }, { id: "b", name: "Stone 2", date: "2026-10-01" }, { id: "a", name: "Wood", date: "2026-09-01" }];
  const sorted = (sort: string) => sortDatabaseRecords(records, sort, item => item.name, item => item.date).map(item => item.id);
  expect(sorted("created-desc")).toEqual(["b", "a", "c"]);
  expect(sorted("created-asc")).toEqual(["a", "b", "c"]);
  expect(sorted("name-asc")).toEqual(["b", "c", "a"]);
  expect(sorted("name-desc")).toEqual(["a", "c", "b"]);
  expect(records.map(item => item.id)).toEqual(["c", "b", "a"]);
});
