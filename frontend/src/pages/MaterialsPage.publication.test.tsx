import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialsPage } from "./MaterialsPage";
import { mockApiClient } from "../api/client";
import { materialFromDto, type Material } from "../api/materialDto";
import { localPublicationClient, localPublicationPreview } from "../api/localPublicationClient";
import { publicationPreviewDto } from "../test/publicationFixtures";
import { materialDto, processorDto } from "../test/materialFixtures";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";

beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable:true, value() { this.removeAttribute("open"); } });
});
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });
const first = materialFromDto(materialDto);
const second = { ...first, id: "00000000-0000-4000-8000-000000000099", materialName: "Another material" };
function setup(role: Role = "ADMIN", rows: Material[] = [first, second]) {
  const getMaterials = vi.fn().mockImplementation(async (filters: { search?: string }) => filters.search ? [first] : rows);
  const preview = vi.spyOn(localPublicationClient, "preview").mockResolvedValue(localPublicationPreview(publicationPreviewDto(), [first.id]));
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialsPage client={{ ...mockApiClient, getMaterials }} navigate={vi.fn()} />
  </SessionContext.Provider>);
  return { getMaterials, preview };
}
it("prepares only checked materials and preserves filters on return", async () => {
  const { getMaterials, preview } = setup();
  await screen.findByRole("table");
  expect(screen.getByRole("heading", { name: "Materials" }).closest("section")).toHaveClass("database-page--workspace");
  expect(screen.queryByRole("button", { name: /Prepare filtered/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Publication batches" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Prepare for publication (0)" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox", { name: "Search materials" }), { target: { value: "#Autumn" } });
  await waitFor(() => expect(getMaterials).toHaveBeenLastCalledWith({ search: "#Autumn" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: `Select ${first.materialName}` }));
  fireEvent.click(screen.getByRole("button", { name: "Prepare for publication (1)" }));
  expect(screen.getByRole("heading", { name: "Materials" }).closest("section")).not.toHaveClass("database-page--workspace");
  expect(screen.getByRole("searchbox", { name: "Search materials" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Review materials" }));
  await waitFor(() => expect(preview).toHaveBeenCalledWith([first.id]));
  await waitFor(() => expect(screen.getByRole("button", { name: "Back to material list" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Back to material list" }));
  expect(screen.getByRole("searchbox")).toHaveValue("#Autumn");
  expect(screen.getByRole("table")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Materials" }).closest("section")).toHaveClass("database-page--workspace");
});
it("lets Production Lead prepare a selected subset", async () => {
  const { preview } = setup("PRODUCTION_LEAD");
  const table = await screen.findByRole("table");
  fireEvent.click(within(table).getByRole("checkbox", { name: `Select ${first.materialName}` }));
  fireEvent.click(screen.getByRole("button", { name: "Prepare for publication (1)" }));
  fireEvent.click(screen.getByRole("button", { name: "Review materials" }));
  await waitFor(() => expect(preview).toHaveBeenCalledWith([first.id]));
});
it("does not silently truncate a selection larger than 100 materials", async () => {
  setup("ADMIN", Array.from({ length: 101 }, (_, i) => ({ ...first, id: `00000000-0000-4000-8000-${String(i).padStart(12,"0")}` })));
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select all visible materials" }));
  expect(screen.getByRole("button", { name: "Prepare for publication (101)" })).toBeDisabled();
  expect(screen.getByText(/Select up to 100 rows/)).toBeVisible();
}, 15000);
it.each<Role>(["PROCESSOR", "LEADERSHIP"])("does not expose desktop publication to %s", async role => {
  setup(role); await screen.findByRole("table");
  expect(screen.queryByRole("button", { name: /Prepare .* for publication/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Publication batches" })).not.toBeInTheDocument();
});
