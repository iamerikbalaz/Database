import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EditableResourceTable } from "./EditableResourceTable";
const rows = ["First", "Second", "Third", "Fourth"].map((name, index) => ({ id: String(index), name }));
function table(items = rows) { return <EditableResourceTable rows={items} columns={[{ key: "name", label: "Name", value: item => item.name, editable: true, bulk: true }]} label={item => item.name} save={vi.fn()} canEdit refresh={vi.fn()} storageKey="test-selection" />; }
const row = (name: string) => screen.getByRole("row", { name: `Record row ${name}` });
afterEach(() => localStorage.clear());
it("shares Materials click, Shift, Ctrl and explicit selection behavior", () => {
  render(table());
  fireEvent.click(row("First")); fireEvent.click(row("Third"), { shiftKey: true });
  expect(row("Second")).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("checkbox", { name: "Select First" })).not.toBeChecked();
  fireEvent.click(row("Second"), { ctrlKey: true }); fireEvent.click(row("Fourth"), { metaKey: true });
  expect(row("Second")).toHaveAttribute("aria-selected", "false");
  fireEvent.click(screen.getByRole("button", { name: "Select highlighted (3)" }));
  expect(screen.getByRole("checkbox", { name: "Select First" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select Second" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select all filtered rows" })).toBePartiallyChecked();
  fireEvent.click(screen.getByRole("textbox", { name: "Name for Second" }));
  expect(row("Second")).toHaveAttribute("aria-selected", "false");
});
it("prunes both highlights and selection when a filter removes rows", () => {
  const view = render(table()); fireEvent.click(row("First")); fireEvent.click(row("Fourth"), { shiftKey: true });
  fireEvent.click(screen.getByRole("button", { name: "Select highlighted (4)" }));
  view.rerender(table([rows[1]])); view.rerender(table());
  expect(screen.getByRole("button", { name: "Select highlighted (1)" })).toBeEnabled();
  expect(screen.getByRole("checkbox", { name: "Select First" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select Second" })).toBeChecked();
});
