import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { NasFolderReference } from "./NasFolderReference";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it("copies the full NAS path from the compact icon and preserves the original text for manual copying", async () => {
  const path = "R:\\0. PROJECTS\\0246_EXAMPLE_SCANNING_FABRICS_102026";
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  render(<NasFolderReference path={path} />);
  const button = screen.getByRole("button", { name: "Copy folder path" });
  expect(button).toHaveTextContent(""); expect(screen.getByTitle(path)).toHaveTextContent(path);
  fireEvent.click(button);
  expect(writeText).toHaveBeenCalledExactlyOnceWith(path);
  expect(await screen.findByRole("status")).toHaveTextContent("Path copied");
});
