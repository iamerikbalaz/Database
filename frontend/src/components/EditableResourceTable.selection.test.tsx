import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EditableResourceTable } from "./EditableResourceTable";
const rows = ["First", "Second", "Third", "Fourth"].map((name, index) => ({ id: String(index), name }));
function table(items = rows) { return <EditableResourceTable rows={items} columns={[{ key: "name", label: "Name", value: item => item.name, editable: true, bulk: true }]} label={item => item.name} save={vi.fn()} canEdit refresh={vi.fn()} storageKey="test-selection" />; }
const row = (name: string) => screen.getByRole("row", { name: `Record row ${name}` });
afterEach(() => localStorage.clear());
it("places row folder actions immediately after selection and keeps the action independent", () => {
  const openFolder = vi.fn();
  render(<EditableResourceTable rows={rows} columns={[{ key: "name", label: "Name", value: item => item.name }]}
    label={item => item.name} save={vi.fn()} canEdit refresh={vi.fn()} storageKey="test-selection"
    leadingAction={item => <button onClick={() => openFolder(item.id)}>Open folder for {item.name}</button>} />);
  const checkbox = screen.getByRole("checkbox", { name: "Select First" });
  const folder = screen.getByRole("button", { name: "Open folder for First" });
  expect(checkbox.parentElement).toHaveClass("record-leading-actions");
  expect(checkbox.parentElement?.firstElementChild).toBe(checkbox);
  expect(checkbox.nextElementSibling).toBe(folder);
  fireEvent.click(folder);
  expect(openFolder).toHaveBeenCalledWith(rows[0].id);
  expect(checkbox).not.toBeChecked();
  expect(row("First")).toHaveAttribute("aria-selected", "false");
});
it("keeps folder actions available to readers without adding selection controls", () => {
  render(<EditableResourceTable rows={rows} columns={[{ key: "name", label: "Name", value: item => item.name }]}
    label={item => item.name} save={vi.fn()} canEdit={false} refresh={vi.fn()} storageKey="test-selection"
    leadingAction={item => <button>Open folder for {item.name}</button>} />);
  expect(screen.getByRole("button", { name: "Open folder for First" })).toBeVisible();
  expect(screen.queryByRole("checkbox", { name: /Select / })).not.toBeInTheDocument();
});
it("shares Materials click, Shift, Ctrl and explicit selection behavior", () => {
  render(table());
  expect(screen.queryByRole("combobox", { name: "Bulk property" })).not.toBeInTheDocument();
  fireEvent.click(row("First")); fireEvent.click(row("Third"), { shiftKey: true });
  expect(row("Second")).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("checkbox", { name: "Select First" })).not.toBeChecked();
  fireEvent.click(row("Second"), { ctrlKey: true }); fireEvent.click(row("Fourth"), { metaKey: true });
  expect(row("Second")).toHaveAttribute("aria-selected", "false");
  fireEvent.click(screen.getByRole("button", { name: "Select highlighted (3)" }));
  expect(screen.getByRole("checkbox", { name: "Select First" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select Second" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select all filtered rows" })).toBePartiallyChecked();
  expect(screen.getByRole("combobox", { name: "Bulk property" })).toBeVisible();
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
  fireEvent.click(screen.getByRole("checkbox", { name: "Select Second" }));
  expect(screen.queryByRole("combobox", { name: "Bulk property" })).not.toBeInTheDocument();
});
