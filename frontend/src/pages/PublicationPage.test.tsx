import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PublicationPage } from "./PublicationPage";
import { mockApiClient } from "../api/client";
import { materialFromDto } from "../api/materialDto";
import { ApiError } from "../api/errors";
import { publicationBatchFromDto, publicationClient, publicationPreviewFromDto } from "../api/publicationClient";
import { publicationBatchDto, publicationId, publicationPreviewDto } from "../test/publicationFixtures";
import { materialDto, processorDto } from "../test/materialFixtures";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function setup(role: Role = "ADMIN", warning = false) {
  const dto = publicationPreviewDto();
  if (warning) dto.items[0].warnings.push({ material_id: materialDto.id, code: "CSV_FORMULA_LIKE_VALUE", fields: ["description"] });
  const preview = vi.spyOn(publicationClient, "preview").mockResolvedValue(publicationPreviewFromDto(dto, [materialDto.id]));
  const create = vi.spyOn(publicationClient, "create").mockResolvedValue(publicationBatchFromDto(publicationBatchDto()));
  const history = vi.spyOn(publicationClient, "history").mockResolvedValue({ items: [publicationBatchFromDto(publicationBatchDto())], nextCursor: null });
  const detail = vi.spyOn(publicationClient, "detail").mockResolvedValue(publicationBatchFromDto(publicationBatchDto()));
  const csv = vi.spyOn(publicationClient, "csv").mockResolvedValue(new Blob(["synthetic"]));
  const getMaterials = vi.fn().mockResolvedValue([materialFromDto({ ...materialDto, workflow_status: "DONE" })]);
  const getProjects = vi.fn().mockResolvedValue([]);
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <PublicationPage client={{ ...mockApiClient, getMaterials, getProjects }} navigate={vi.fn()} />
  </SessionContext.Provider>);
  return { preview, create, history, detail, csv, getMaterials, getProjects };
}
async function inspect() {
  fireEvent.click(screen.getByRole("button", { name: "Find materials" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: new RegExp(materialDto.material_name) }));
  fireEvent.click(screen.getByRole("button", { name: "Review selected materials" }));
  await screen.findByText("All selected materials passed the current approval checks.");
}
function review() {
  fireEvent.change(screen.getByLabelText("Reason for preparing this batch"), { target: { value: "Prepare synthetic batch" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "I reviewed the selected materials and export values." }));
}
it("requires explicit selection, preview and review before saving an immutable batch", async () => {
  const { getMaterials, preview, create } = setup();
  expect(getMaterials).not.toHaveBeenCalled(); expect(preview).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Review selected materials" })).toBeDisabled();
  await inspect(); expect(screen.getByRole("button", { name: "Save CSV batch" })).toBeDisabled(); review();
  fireEvent.click(screen.getByRole("button", { name: "Save CSV batch" }));
  await screen.findByText(/CSV batch saved/);
  expect(create).toHaveBeenCalledWith({ material_ids: [materialDto.id], idempotency_key: expect.any(String), expected_preview_hash: "c".repeat(64), reason: "Prepare synthetic batch", warnings_acknowledged: false });
  expect(screen.getByText(/This CSV is a historical snapshot/)).toBeVisible(); expect(screen.queryByRole("button", { name: "Save CSV batch" })).not.toBeInTheDocument();
});
it("requires an explicit warning acknowledgment and displays formula-like text guidance", async () => {
  const { create } = setup("LEADERSHIP", true); await inspect(); review();
  expect(screen.getByText(/spreadsheet software may execute it as a formula/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Save CSV batch" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "I acknowledge the export warnings above." }));
  fireEvent.click(screen.getByRole("button", { name: "Save CSV batch" })); await screen.findByText(/CSV batch saved/);
  expect(create.mock.calls[0][0].warnings_acknowledged).toBe(true);
});
it("freezes uncertain writes and retries the same key and exact payload", async () => {
  const { create } = setup(); create.mockRejectedValueOnce(new TypeError("Synthetic timeout")); await inspect(); review();
  fireEvent.click(screen.getByRole("button", { name: "Save CSV batch" })); await screen.findByText(/The outcome is unknown/);
  for (const name of ["Find materials", "Load latest batches", "Save CSV batch"]) expect(screen.getByRole("button", { name })).toBeDisabled();
  expect(screen.getByLabelText("Reason for preparing this batch")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry same batch request" })); await screen.findByText(/CSV batch saved/);
  expect(create.mock.calls[0]).toEqual(create.mock.calls[1]);
});
it("requires a new preview after the server rejects stale approval inputs", async () => {
  const { create } = setup(); create.mockRejectedValueOnce(new ApiError(409, "Conflict")); await inspect(); review();
  fireEvent.click(screen.getByRole("button", { name: "Save CSV batch" })); await screen.findByText(/Inputs or approvals changed/);
  expect(screen.queryByRole("button", { name: "Save CSV batch" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Retry same batch request" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Review selected materials" })).toBeEnabled();
});
it("invalidates preview and review acknowledgments when selection changes", async () => {
  setup(); await inspect(); review(); fireEvent.click(screen.getByRole("button", { name: `Remove ${materialDto.technical_identity}` }));
  expect(screen.queryByRole("button", { name: "Save CSV batch" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Review selected materials" })).toBeDisabled();
});
it("shows blocking findings and never permits an unapproved batch", async () => {
  const { preview, create } = setup(); const dto = publicationPreviewDto(); dto.can_prepare = false; dto.items[0].errors = ["CONTENT_APPROVAL_REQUIRED"];
  preview.mockResolvedValue(publicationPreviewFromDto(dto, [materialDto.id]));
  fireEvent.click(screen.getByRole("button", { name: "Find materials" })); fireEvent.click(await screen.findByRole("checkbox", { name: new RegExp(materialDto.material_name) }));
  fireEvent.click(screen.getByRole("button", { name: "Review selected materials" })); await screen.findByText("content approval required"); review();
  expect(screen.getByRole("button", { name: "Save CSV batch" })).toBeDisabled(); expect(create).not.toHaveBeenCalled();
});
it("loads historical details without starting a new preview or save", async () => {
  const { history, detail, preview, create } = setup(); history.mockResolvedValueOnce({ items: [publicationBatchFromDto(publicationBatchDto())], nextCursor: publicationId });
  fireEvent.click(screen.getByRole("button", { name: "Load latest batches" })); fireEvent.click(await screen.findByRole("button", { name: "Older batches" }));
  await waitFor(() => expect(history).toHaveBeenLastCalledWith(publicationId));
  fireEvent.click(screen.getByRole("button", { name: `Open batch ${publicationId}` })); await screen.findByText(/This CSV is a historical snapshot/);
  expect(detail).toHaveBeenCalledWith(publicationId); expect(preview).not.toHaveBeenCalled(); expect(create).not.toHaveBeenCalled();
});
it("does not offer unchecked bytes when the download integrity check fails", async () => {
  const { csv } = setup(); csv.mockRejectedValue(new Error("Synthetic integrity failure"));
  fireEvent.click(screen.getByRole("button", { name: "Load latest batches" })); fireEvent.click(await screen.findByRole("button", { name: `Open batch ${publicationId}` }));
  fireEvent.click(await screen.findByRole("button", { name: "Download saved CSV" })); await screen.findByText(/CSV download or its integrity check failed/);
  expect(screen.queryByText(/ready to download/)).not.toBeInTheDocument();
});
it.each(["PROCESSOR", "PRODUCTION_LEAD"] as Role[])("does not load publication data for %s", (role) => {
  const { getMaterials, getProjects, preview, history } = setup(role);
  expect(screen.getByText(/Your role does not allow/)).toBeVisible();
  for (const call of [getMaterials, getProjects, preview, history]) expect(call).not.toHaveBeenCalled();
});
