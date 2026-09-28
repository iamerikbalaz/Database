import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { MaterialColorSelect } from "./MaterialColorSelect";
import { materialColors } from "../data/materialColors";

function Interactive({ initial = "" }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  return <MaterialColorSelect value={value} onChange={setValue} />;
}

it("shows real swatches for each palette choice and the selected recorded color", () => {
  render(<Interactive initial="#A1B2C3" />);
  const combo = screen.getByRole("combobox", { name: "Color HEX" });
  expect(combo).toHaveTextContent("#A1B2C3 (recorded value)");
  expect(combo.querySelector(".material-color-swatch")).toHaveStyle({ backgroundColor: "#A1B2C3" });
  fireEvent.click(combo);
  expect(screen.getAllByRole("option")).toHaveLength(materialColors.length + 2);
  for (const color of materialColors) expect(screen.getByRole("option", { name: color }).querySelector(".material-color-swatch")).toHaveStyle({ backgroundColor: color });
  expect(screen.getByRole("option", { name: "#A1B2C3 (recorded value)" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("option", { name: "#999999" }));
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  expect(combo).toHaveTextContent("#999999");
  expect(combo.querySelector(".material-color-swatch")).toHaveStyle({ backgroundColor: "#999999" });
  expect(combo).toHaveFocus();
  fireEvent.click(combo);
  fireEvent.click(screen.getByRole("option", { name: "No color" }));
  expect(combo).toHaveTextContent("No color");
});

it("supports keyboard navigation, explicit selection, escape cancellation and tab dismissal", () => {
  render(<Interactive />);
  const combo = screen.getByRole("combobox", { name: "Color HEX" });
  combo.focus();
  fireEvent.keyDown(combo, { key: "ArrowDown" });
  expect(combo).toHaveAttribute("aria-expanded", "true");
  fireEvent.keyDown(combo, { key: "End" });
  expect(combo).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "#666666" }).id);
  expect(combo).toHaveTextContent("No color");
  fireEvent.keyDown(combo, { key: "Enter" });
  expect(combo).toHaveTextContent("#666666");
  fireEvent.keyDown(combo, { key: " " });
  fireEvent.keyDown(combo, { key: "Home" });
  fireEvent.keyDown(combo, { key: "Escape" });
  expect(combo).toHaveTextContent("#666666");
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  fireEvent.keyDown(combo, { key: "Home" });
  fireEvent.keyDown(combo, { key: "ArrowDown" });
  fireEvent.keyDown(combo, { key: "Enter" });
  expect(combo).toHaveTextContent("#CCCC99");
  fireEvent.keyDown(combo, { key: "ArrowUp" });
  fireEvent.keyDown(combo, { key: "Tab" });
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});

it("finds HEX options by typing and dismisses on an outside click without changing the color", () => {
  const change = vi.fn();
  render(<MaterialColorSelect value="" onChange={change} />);
  const combo = screen.getByRole("combobox", { name: "Color HEX" });
  fireEvent.keyDown(combo, { key: "F" }); fireEvent.keyDown(combo, { key: "F" });
  const list = screen.getByRole("listbox");
  expect(combo).toHaveAttribute("aria-activedescendant", within(list).getByRole("option", { name: "#FFFFFF" }).id);
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  expect(change).not.toHaveBeenCalled();
});

it("keeps a disabled palette closed", () => {
  render(<MaterialColorSelect value="#000000" onChange={vi.fn()} disabled />);
  const combo = screen.getByRole("combobox", { name: "Color HEX" });
  expect(combo).toBeDisabled(); fireEvent.click(combo);
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});
