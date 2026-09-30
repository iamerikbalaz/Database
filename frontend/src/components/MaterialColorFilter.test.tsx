import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MaterialColorFilter } from "./MaterialColorFilter";

afterEach(() => vi.restoreAllMocks());

function Demo({ initial = [] }: { initial?: string[] }) {
  const [value, setValue] = useState(initial);
  return <><MaterialColorFilter value={value} onChange={setValue} /><output>{value.join(",")}</output></>;
}
it("selects several named swatches, keeps popup open, and clears without losing keyboard access", () => {
  render(<Demo />);
  const combo = screen.getByRole("combobox", { name: "Color" });
  fireEvent.click(combo);
  const red = screen.getByRole("option", { name: /Red/ });
  expect(red.querySelector(".material-color-swatch")).toHaveStyle({ backgroundColor: "#FF0000" });
  fireEvent.click(red);
  fireEvent.click(screen.getByRole("option", { name: /Blue #0000FF/ }));
  expect(screen.getByRole("status")).toHaveTextContent("#FF0000,#0000FF");
  expect(screen.getByRole("listbox")).toHaveAttribute("aria-multiselectable", "true");
  expect(combo).toHaveTextContent("2 colors");
  expect(red).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(combo, { key: "Escape" });
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  fireEvent.keyDown(combo, { key: "w" });
  fireEvent.keyDown(combo, { key: "Enter" });
  expect(screen.getByRole("status")).toHaveTextContent("#FF0000,#0000FF,#FFFFFF");
  fireEvent.click(screen.getByRole("button", { name: "All colors" }));
  expect(screen.getByRole("status")).toBeEmptyDOMElement();
  expect(combo).toHaveTextContent("All colors");
});
it("shows a preserved non-palette value by its HEX without inventing a platform match", () => {
  render(<Demo initial={["#123456"]} />);
  fireEvent.click(screen.getByRole("combobox"));
  expect(screen.getByRole("option", { name: /Custom #123456/ })).toHaveAttribute("aria-selected", "true");
});
it("opens above a low filter within clipped workspace bounds and recalculates on resize", () => {
  let anchorTop = 400;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const top = this.classList.contains("material-color-filter") ? anchorTop : this.dataset.clip ? 120 : 0;
    const height = this.classList.contains("material-color-filter") ? 50 : this.dataset.clip ? 530 : 0;
    return { x: 0, y: top, top, bottom: top + height, left: 0, right: 200, width: 200, height, toJSON: () => ({}) };
  });
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function (this: HTMLElement) { return this.dataset.clip ? 530 : 0; });
  render(<div data-clip="true" style={{ overflowY: "hidden" }}><Demo /></div>);
  const combo = screen.getByRole("combobox", { name: "Color" });
  fireEvent.click(combo);
  const popup = screen.getByRole("listbox").parentElement!;
  expect(popup).toHaveAttribute("data-placement", "above");
  expect(popup.style.getPropertyValue("--material-color-popup-height")).toBe("272px");
  fireEvent.keyDown(combo, { key: "End" });
  const last = screen.getAllByRole("option").at(-1)!;
  expect(combo).toHaveAttribute("aria-activedescendant", last.id);
  fireEvent.keyDown(combo, { key: "Enter" });
  expect(last).toHaveAttribute("aria-selected", "true");
  anchorTop = 180;
  fireEvent(window, new Event("resize"));
  expect(popup).toHaveAttribute("data-placement", "below");
  expect(popup.style.getPropertyValue("--material-color-popup-height")).toBe("288px");
  fireEvent.keyDown(combo, { key: "Escape" });
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});
