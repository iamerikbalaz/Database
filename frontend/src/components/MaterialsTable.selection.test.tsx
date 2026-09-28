import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialsTable } from "./MaterialsTable";
import { mockApiClient } from "../api/client";
import { GalleryStore } from "../api/galleryStore";
import { materialFromDto } from "../api/materialDto";
import { SessionContext } from "../auth/context";
import { materialDto, processorDto } from "../test/materialFixtures";

const rows = ["FIRST", "SECOND", "THIRD", "FOURTH"].map((materialName, index) => ({ ...materialFromDto(materialDto), materialName, id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}` }));
const store = new GalleryStore();
const renderTable = (materials = rows) => <SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
  <MaterialsTable materials={materials} store={store} client={mockApiClient} projects={[]} brands={[]} users={[]} navigate={vi.fn()} refresh={vi.fn()} onBusyChange={vi.fn()} />
</SessionContext.Provider>;
const row = (name: string) => screen.getByRole("row", { name: `Material row ${name}` });
beforeEach(() => localStorage.clear());
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });
it("highlights with click/Shift/Ctrl independently from checked selection and adds highlighted rows explicitly", () => {
  render(renderTable());
  fireEvent.click(row("FIRST"));
  fireEvent.click(row("THIRD"), { shiftKey: true });
  ["FIRST", "SECOND", "THIRD"].forEach(name => expect(row(name)).toHaveAttribute("aria-selected", "true"));
  expect(screen.getByRole("checkbox", { name: "Select FIRST" })).not.toBeChecked();
  fireEvent.click(row("SECOND"), { ctrlKey: true });
  expect(row("SECOND")).toHaveAttribute("aria-selected", "false");
  fireEvent.click(row("FOURTH"), { metaKey: true });
  fireEvent.click(screen.getByRole("checkbox", { name: "Select SECOND" }));
  fireEvent.click(screen.getByRole("button", { name: "Select highlighted (3)" }));
  ["FIRST", "SECOND", "THIRD", "FOURTH"].forEach(name => expect(screen.getByRole("checkbox", { name: `Select ${name}` })).toBeChecked());
  fireEvent.click(row("SECOND"));
  expect(row("FIRST")).toHaveAttribute("aria-selected", "false");
  expect(row("SECOND")).toHaveAttribute("aria-selected", "true");
});
it("adds Ctrl+Shift ranges and leaves inline controls and material links alone", () => {
  render(renderTable());
  fireEvent.click(row("FIRST"));
  fireEvent.click(row("THIRD"), { ctrlKey: true });
  fireEvent.click(row("FOURTH"), { ctrlKey: true, shiftKey: true });
  expect(row("SECOND")).toHaveAttribute("aria-selected", "false");
  ["FIRST", "THIRD", "FOURTH"].forEach(name => expect(row(name)).toHaveAttribute("aria-selected", "true"));
  fireEvent.click(within(row("SECOND")).getByRole("textbox"));
  fireEvent.click(within(row("SECOND")).getByRole("combobox", { name: "Status for SECOND" }));
  fireEvent.click(within(row("SECOND")).getByRole("link", { name: "SECOND" }));
  expect(row("SECOND")).toHaveAttribute("aria-selected", "false");
});
it("prunes vanished highlights and checked IDs so they do not reappear or enter later operations", () => {
  const tree = render(renderTable());
  fireEvent.click(row("FIRST"));
  fireEvent.click(row("FOURTH"), { shiftKey: true });
  fireEvent.click(screen.getByRole("button", { name: "Select highlighted (4)" }));
  tree.rerender(renderTable([rows[2]]));
  expect(screen.getByRole("button", { name: "Select highlighted (1)" })).toBeEnabled();
  tree.rerender(renderTable());
  expect(row("FIRST")).toHaveAttribute("aria-selected", "false");
  expect(screen.getByRole("checkbox", { name: "Select FIRST" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select THIRD" })).toBeChecked();
});
it("shows the automatic check as readonly labels distinct from manual Checked", () => {
  render(renderTable(rows.slice(0, 3).map((material, index) => ({ ...material, automaticFileCheckStatus: (["NOT_CHECKED", "OK", "ISSUES"] as const)[index] }))));
  expect(screen.getByRole("columnheader", { name: "Automatic file check" })).toBeVisible();
  expect(within(row("FIRST")).getByText("not checked")).toHaveClass("automatic-file-check--not_checked");
  expect(within(row("SECOND")).getByText("OK", { selector: ".automatic-file-check" })).toHaveClass("automatic-file-check--ok");
  expect(within(row("THIRD")).getByText("issues")).toHaveClass("automatic-file-check--issues");
  expect(screen.queryByRole("combobox", { name: /Automatic file check/ })).not.toBeInTheDocument();
});
