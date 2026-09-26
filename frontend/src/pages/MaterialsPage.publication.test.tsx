import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialsPage } from "./MaterialsPage";
import { mockApiClient } from "../api/client";
import { materialFromDto, type Material } from "../api/materialDto";
import { publicationClient, publicationPreviewFromDto } from "../api/publicationClient";
import { publicationPreviewDto } from "../test/publicationFixtures";
import { materialDto, processorDto } from "../test/materialFixtures";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";

beforeEach(() => localStorage.clear());
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });
const first = materialFromDto(materialDto);
const second = { ...first, id: "00000000-0000-4000-8000-000000000099", materialName: "Another material" };
function setup(role: Role = "ADMIN", rows: Material[] = [first, second]) {
  const getMaterials = vi.fn().mockImplementation(async (filters: { search?: string }) => filters.search ? [first] : rows);
  const preview = vi.spyOn(publicationClient, "preview").mockResolvedValue(publicationPreviewFromDto(publicationPreviewDto(), [first.id]));
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsPage client={{ ...mockApiClient, getMaterials }} navigate={vi.fn()} />
  </SessionContext.Provider>);
  return { getMaterials, preview };
}
it("prepares exactly the current filtered records and preserves note filters on return", async () => {
  const { getMaterials, preview } = setup();
  await screen.findByRole("table");
  fireEvent.change(screen.getByRole("searchbox", { name: "Search materials" }), { target: { value: "#Autumn" } });
  await waitFor(() => expect(getMaterials).toHaveBeenLastCalledWith({ search: "#Autumn" }));
  fireEvent.click(await screen.findByRole("button", { name: "Prepare filtered for publication (1)" }));
  expect(screen.getByRole("searchbox")).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Find materials" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Remove/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Review selected materials" }));
  await waitFor(() => expect(preview).toHaveBeenCalledWith([first.id]));
  await screen.findByText("All selected materials passed the current approval checks.");
  fireEvent.click(screen.getByRole("button", { name: "Back to material list" }));
  expect(screen.getByRole("searchbox")).toHaveValue("#Autumn");
  expect(screen.getByRole("table")).toBeVisible();
});
it("lets Leadership prepare a selected subset without granting inline edit controls", async () => {
  const { preview } = setup("LEADERSHIP");
  const table = await screen.findByRole("table");
  expect(within(table).queryByRole("combobox")).not.toBeInTheDocument();
  fireEvent.click(within(table).getByRole("checkbox", { name: `Select ${first.materialName}` }));
  fireEvent.click(screen.getByRole("button", { name: "Prepare selected for publication (1)" }));
  expect(screen.getByRole("heading", { name: "Selected materials (1/100)" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Review selected materials" }));
  await waitFor(() => expect(preview).toHaveBeenCalledWith([first.id]));
});
it("requires narrowing more than 100 filtered records and does not silently truncate them", async () => {
  setup("ADMIN", Array.from({ length: 101 }, (_, i) => ({ ...first, id: `material-${i}` })));
  await screen.findByRole("table");
  expect(screen.getByRole("button", { name: "Prepare filtered for publication (101)" })).toBeDisabled();
  expect(screen.getByText(/Narrow the filters or select up to 100/)).toBeVisible();
});
it("does not expose publication preparation to Processor", async () => {
  setup("PROCESSOR"); await screen.findByRole("table");
  expect(screen.queryByRole("button", { name: /Prepare .* for publication/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Publication batches" })).not.toBeInTheDocument();
});
