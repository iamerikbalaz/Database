import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialsPage } from "./MaterialsPage";
import { mockApiClient } from "../api/client";
import { materialFromDto, type Material } from "../api/materialDto";
import { materialDto, processorDto } from "../test/materialFixtures";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";

vi.mock("../components/MaterialAiBriefDialog", () => ({
  MaterialAiBriefDialog: ({ materials, onClose, onChanged }: { materials: Material[]; onClose: () => void; onChanged: () => void }) =>
    <div role="dialog" aria-label="AI descriptions"><output>{materials.map(row => row.id).join(",")}</output><button onClick={onChanged}>Saved AI descriptions</button><button onClick={onClose}>Close AI descriptions</button></div>,
}));
const first = materialFromDto(materialDto);
const second = { ...first, id: "50000000-0000-4000-8000-000000000002", materialName: "SECOND" };
beforeEach(() => localStorage.clear());
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });
function setup(role: Role, gallery = false, archived = false, rows = [first, second]) {
  const getMaterials = vi.fn().mockResolvedValue(rows.map(row => ({ ...row, isArchived: archived })));
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsPage client={{ ...mockApiClient, getMaterials }} navigate={vi.fn()} archived={archived} initialView={gallery ? "gallery" : undefined} />
  </SessionContext.Provider>);
  return getMaterials;
}

it.each([false, true])("passes only selected materials to the AI dialog and locks competing actions (gallery=%s)", async gallery => {
  const getMaterials = setup("ADMIN", gallery);
  await screen.findByRole(gallery ? "list" : "table", gallery ? { name: "Material gallery" } : {});
  expect(screen.queryByRole("button", { name: "AI descriptions" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: `Select ${second.materialName}` }));
  fireEvent.click(screen.getByRole("button", { name: "AI descriptions" }));
  expect(screen.getByRole("dialog", { name: "AI descriptions" })).toHaveTextContent(second.id);
  expect(screen.getByRole("dialog", { name: "AI descriptions" })).not.toHaveTextContent(first.id);
  expect(screen.getByRole("button", { name: "Delete selected materials" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: `Select ${first.materialName}` })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Prepare for publication (1)" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Saved AI descriptions" }));
  await waitFor(() => expect(getMaterials).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole("button", { name: "Close AI descriptions" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "AI descriptions" })).toBeEnabled();
});

it("allows a production lead to prepare descriptions", async () => {
  setup("PRODUCTION_LEAD");
  fireEvent.click(await screen.findByRole("checkbox", { name: `Select ${first.materialName}` }));
  expect(screen.getByRole("button", { name: "AI descriptions" })).toBeEnabled();
});

it.each<[Role, boolean]>([["PROCESSOR", false], ["LEADERSHIP", false], ["ADMIN", true]])("does not offer AI editing for role=%s archived=%s", async (role, archived) => {
  setup(role, false, archived); await screen.findByRole("table");
  const checkbox = screen.queryByRole("checkbox", { name: `Select ${first.materialName}` });
  if (checkbox) fireEvent.click(checkbox);
  expect(screen.queryByRole("button", { name: "AI descriptions" })).not.toBeInTheDocument();
});

it("limits AI batches to 100 explicitly selected materials", async () => {
  setup("ADMIN", true, false, Array.from({ length: 101 }, (_, index) => ({ ...first, id: `50000000-0000-4000-8000-${String(index).padStart(12, "0")}`, materialName: `MATERIAL-${index}` })));
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select all visible materials" }));
  expect(screen.getByRole("button", { name: "AI descriptions" })).toBeDisabled();
});
