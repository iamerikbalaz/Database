import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { MaterialColorFilter } from "./MaterialColorFilter";

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
