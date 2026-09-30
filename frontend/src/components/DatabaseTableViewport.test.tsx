import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DatabaseTableViewport } from "./DatabaseTableViewport";

afterEach(() => vi.restoreAllMocks());
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
