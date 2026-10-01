import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DatabaseTableViewport } from "./DatabaseTableViewport";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it.each(["page", "contained"] as const)("routes horizontal thumb-wheel and Shift-wheel input in %s mode without hijacking vertical scrolling", scrollMode => {
  render(<DatabaseTableViewport label="Records" scrollMode={scrollMode}><table><thead><tr><th>Name</th></tr></thead><tbody><tr><td>Example</td></tr></tbody></table></DatabaseTableViewport>);
  const body = screen.getByRole("region", { name: "Records" }), top = screen.getByRole("region", { name: "Records horizontal scroll" });
  Object.defineProperties(body, { scrollWidth: { value: 1600 }, clientWidth: { value: 600 } });
  const wheel = (target: Element, init: WheelEventInit) => { const event = new WheelEvent("wheel", { bubbles: true, cancelable: true, ...init }); fireEvent(target, event); return event; };
  expect(wheel(screen.getByText("Example"), { deltaX: 120 }).defaultPrevented).toBe(true);
  expect(body.scrollLeft).toBe(120); expect(top.scrollLeft).toBe(120);
  wheel(top, { deltaY: 3, deltaMode: 1, shiftKey: true });
  expect(body.scrollLeft).toBe(168); expect(top.scrollLeft).toBe(168);
  wheel(body, { deltaX: 1, deltaMode: 2 }); expect(body.scrollLeft).toBe(768);
  wheel(body, { deltaX: 600 }); expect(body.scrollLeft).toBe(1000);
  expect(wheel(body, { deltaX: 200 }).defaultPrevented).toBe(false);
  expect(wheel(body, { deltaY: 80 }).defaultPrevented).toBe(false);
  expect(wheel(body, { deltaX: -100, ctrlKey: true }).defaultPrevented).toBe(false);
  expect(body.scrollLeft).toBe(1000);
  wheel(body, { deltaX: -160 }); expect(body.scrollLeft).toBe(840); expect(top.scrollLeft).toBe(840);
});
it("synchronizes the top scrollbar with one semantic table and native cell scrolling", () => {
  render(<DatabaseTableViewport label="Materials"><table><thead><tr><th>Material</th></tr></thead><tbody><tr><td>Stone</td></tr></tbody></table></DatabaseTableViewport>);
  const top = screen.getByRole("region", { name: "Materials horizontal scroll" }), body = screen.getByRole("region", { name: "Materials" });
  top.scrollLeft = 320; fireEvent.scroll(top); expect(body.scrollLeft).toBe(320);
  body.scrollLeft = 640; fireEvent.scroll(body); expect(top.scrollLeft).toBe(640);
  expect(screen.getAllByRole("table")).toHaveLength(1); expect(screen.getAllByRole("columnheader")).toHaveLength(1);
  expect(body).not.toHaveStyle({ maxHeight: "70vh" });
});
it("keeps the real header below the top scrollbar as the page scrolls", () => {
  const dimensions = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    return { top: this.tagName === "TABLE" ? -180 : 0, bottom: this.className === "database-table-scrollbar" ? 20 : 0 } as DOMRect;
  });
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (this: HTMLElement) { return this.tagName === "TABLE" ? 1200 : this.tagName === "THEAD" ? 40 : 0; });
  render(<DatabaseTableViewport label="Customers"><table><thead><tr><th>Customer</th></tr></thead><tbody><tr><td>Example</td></tr></tbody></table></DatabaseTableViewport>);
  expect(screen.getByRole("region", { name: "Customers" }).style.getPropertyValue("--database-header-offset")).toBe("200px");
  expect(dimensions).toHaveBeenCalled();
});

it("lets native sticky headers handle contained vertical scrolling without animation frames or position measurements", () => {
  const listeners = vi.spyOn(window, "addEventListener"), frames = vi.spyOn(window, "requestAnimationFrame");
  const dimensions = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect");
  render(<DatabaseTableViewport label="Customers" scrollMode="contained"><table><thead><tr><th>Customer</th></tr></thead><tbody><tr><td>Example</td></tr></tbody></table></DatabaseTableViewport>);
  const body = screen.getByRole("region", { name: "Customers" });
  body.scrollTop = 500; fireEvent.scroll(body); fireEvent.scroll(window);
  expect(listeners.mock.calls.filter(([name]) => name === "scroll")).toHaveLength(0);
  expect(frames).not.toHaveBeenCalled();
  expect(dimensions).not.toHaveBeenCalled();
  expect(body.style.getPropertyValue("--database-header-offset")).toBe("");
  expect(body.parentElement).toHaveClass("database-table-viewport--contained");
});

it("compensates for the vertical scrollbar gutter so the top scrollbar reaches the final column", () => {
  let resize: (() => void) | undefined;
  const observe = vi.fn(), disconnect = vi.fn();
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: () => void) { resize = callback; }
    observe = observe;
    disconnect = disconnect;
  });
  let resultsWidth = 780;
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
    return this.classList.contains("database-table-scrollbar") ? 800 : this.classList.contains("database-table-body") ? resultsWidth : 0;
  });
  vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockReturnValue(1600);
  const { unmount } = render(<DatabaseTableViewport label="Customers" scrollMode="contained"><table><thead><tr><th>Customer</th></tr></thead><tbody><tr><td>Example</td></tr></tbody></table></DatabaseTableViewport>);
  const top = screen.getByRole("region", { name: "Customers horizontal scroll" }), body = screen.getByRole("region", { name: "Customers" });
  expect(observe.mock.calls.map(([element]) => element)).toEqual([screen.getByRole("table"), body, top]);
  expect(top.firstElementChild).toHaveStyle({ width: "1620px" });
  top.scrollLeft = 820; fireEvent.scroll(top); expect(body.scrollLeft).toBe(820);
  body.scrollLeft = 400; fireEvent.scroll(body); expect(top.scrollLeft).toBe(400);
  const topWrite = vi.fn();
  Object.defineProperty(top, "scrollLeft", { configurable: true, get: () => 400, set: topWrite });
  body.scrollTop = 200; fireEvent.scroll(body);
  expect(topWrite).not.toHaveBeenCalled();
  resultsWidth = 800; resize?.();
  expect(top.firstElementChild).toHaveStyle({ width: "1600px" });
  unmount(); expect(disconnect).toHaveBeenCalledOnce();
});

it("switches modes without replacing the table or its unfinished editor and detaches page scrolling", () => {
  const listeners = vi.spyOn(window, "addEventListener"), removed = vi.spyOn(window, "removeEventListener");
  const contents = <table><thead><tr><th>Customer</th></tr></thead><tbody><tr><td><input aria-label="Customer note" defaultValue="Initial" /></td></tr></tbody></table>;
  const { rerender } = render(<DatabaseTableViewport label="Customers">{contents}</DatabaseTableViewport>);
  const table = screen.getByRole("table"), editor = screen.getByRole("textbox", { name: "Customer note" });
  const pageHandler = listeners.mock.calls.find(([name]) => name === "scroll")?.[1];
  expect(pageHandler).toBeDefined();
  fireEvent.change(editor, { target: { value: "Unfinished edit" } });
  rerender(<DatabaseTableViewport label="Customers" scrollMode="contained">{contents}</DatabaseTableViewport>);
  expect(screen.getByRole("table")).toBe(table);
  expect(screen.getByRole("textbox", { name: "Customer note" })).toBe(editor);
  expect(editor).toHaveValue("Unfinished edit");
  expect(removed).toHaveBeenCalledWith("scroll", pageHandler, true);
  expect(screen.getByRole("region", { name: "Customers" }).style.getPropertyValue("--database-header-offset")).toBe("");
  rerender(<DatabaseTableViewport label="Customers">{contents}</DatabaseTableViewport>);
  expect(screen.getByRole("table")).toBe(table);
  expect(editor).toHaveValue("Unfinished edit");
  expect(table.closest(".database-table-viewport")).not.toHaveClass("database-table-viewport--contained");
});
