import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { ResponsiveFilters } from "./ResponsiveFilters";

afterEach(() => vi.unstubAllGlobals());
function observeWidth() {
  let resized: ResizeObserverCallback | undefined;
  const disconnect = vi.fn();
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: ResizeObserverCallback) { resized = callback; }
    observe() {}
    disconnect = disconnect;
  });
  return { disconnect, resize: (width: number) => act(() => resized?.([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver)) };
}
function Fixture({ compact = true }: { compact?: boolean }) {
  const [date, setDate] = useState("");
  return <ResponsiveFilters compact={compact} label="Filters" onClear={() => setDate("")} filters={[
    { key: "search", width: 190, content: <label>Search<input type="search" /></label> },
    { key: "customer", width: 140, content: <label>Customer<select><option>All</option></select></label> },
    { key: "status", width: 115, content: <label>Status<select><option>All</option></select></label> },
    { key: "due", width: 142, active: Boolean(date), content: <label>Due date<input type="date" value={date} onChange={event => setDate(event.target.value)} /></label> },
  ]} />;
}
it("uses the measured bar width to retain the highest priorities and hides More filters when all fields fit", () => {
  const observer = observeWidth(), view = render(<Fixture />);
  observer.resize(800);
  expect(screen.queryByRole("button", { name: /More filters/ })).not.toBeInTheDocument();
  const due = screen.getByLabelText("Due date");
  fireEvent.change(due, { target: { value: "2026-10-15" } });
  observer.resize(650);
  expect(screen.getByLabelText("Search")).toBeVisible();
  expect(screen.getByLabelText("Customer")).toBeVisible();
  expect(screen.getByLabelText("Status")).not.toBeVisible();
  expect(due).not.toBeVisible();
  const more = screen.getByRole("button", { name: /More filters \(1\)/ });
  expect(more).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(more);
  expect(screen.getByLabelText("Status")).toHaveFocus();
  expect(screen.getByLabelText("Due date")).toBe(due);
  expect(due).toBeVisible(); expect(due).toHaveValue("2026-10-15");
  fireEvent.keyDown(due, { key: "Escape" });
  expect(more).toHaveFocus(); expect(due).not.toBeVisible();
  observer.resize(800);
  expect(screen.getByLabelText("Due date")).toBe(due);
  expect(due).toBeVisible(); expect(due).toHaveValue("2026-10-15");
  expect(screen.queryByRole("button", { name: /More filters/ })).not.toBeInTheDocument();
  view.unmount(); expect(observer.disconnect).toHaveBeenCalledOnce();
});
it("keeps a focused filter available while resizing and preserves the same input across page fallback", () => {
  const observer = observeWidth(), view = render(<Fixture />);
  observer.resize(800);
  const due = screen.getByLabelText("Due date"); due.focus();
  fireEvent.change(due, { target: { value: "2026-12-01" } });
  observer.resize(500);
  expect(screen.getByLabelText("Due date")).toBe(due); expect(due).toHaveFocus(); expect(due).toBeVisible();
  expect(screen.getByRole("button", { name: /More filters/ })).toHaveAttribute("aria-expanded", "true");
  view.rerender(<Fixture compact={false} />);
  expect(screen.queryByRole("button", { name: /More filters/ })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Due date")).toBe(due); expect(due).toHaveValue("2026-12-01");
  view.rerender(<Fixture compact />);
  expect(due).toBeVisible();
  fireEvent.pointerDown(document.body);
  expect(due).not.toBeVisible();
});
