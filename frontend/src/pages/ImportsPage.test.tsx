import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { httpApiClient } from "../api/client";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";
import { setSessionToken } from "../auth/sessionTransport";
import { materialBrand, materialProject, processorDto } from "../test/materialFixtures";
import { importBatch, importCompanies, importInspection, importMappings, importPreview } from "../test/importFixtures";
import { ImportsPage } from "./ImportsPage";

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
function setup(role: Role = "ADMIN") {
  const state = { preview: structuredClone(importPreview), confirm: "success", completed: false, inspections: 0, format: "CSV", selectedSheet: "" };
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.endsWith("/companies")) return json(importCompanies);
    if (path.endsWith("/projects")) return json([materialProject]);
    if (path.endsWith("/brands")) return json([materialBrand]);
    if (path.includes("/internal-users")) return json([processorDto]);
    if (path.includes("/material-imports?")) return json({ items: state.completed ? [importBatch] : [], next_after: null });
    if (path.endsWith("/" + importBatch.id)) return json(importBatch);
    const body = JSON.parse(String(init?.body ?? "{}"));
    if (path.endsWith("/inspect")) {
      state.inspections++; state.format = body.source.format; state.selectedSheet = body.source.sheet ?? "";
      if (body.source.format === "XLSX" && !body.source.sheet) return json({ format: "XLSX", sheets: ["Materials", "Archive"], requires_sheet: true });
      return json({ ...importInspection, format: body.source.format, sheets: body.source.format === "XLSX" ? ["Materials", "Archive"] : [],
        ...(body.columns ? { mapping_values: importMappings } : {}) });
    }
    if (path.endsWith("/preview")) return json(state.preview);
    if (path.endsWith("/confirm")) {
      if (state.confirm === "unknown") throw new TypeError("Synthetic lost response");
      if (state.confirm === "rejected") return json({ detail: { code: "IMPORT_PREVIEW_CHANGED" } }, 409);
      state.completed = true;
      return json({ ...importBatch, idempotency_key: body.idempotency_key, preview_hash: body.expected_preview_hash,
        ...(state.confirm === "mismatch" ? { source_sha256: "c".repeat(64), snapshot: { ...importBatch.snapshot, source_sha256: "c".repeat(64) } } : {}) });
    }
    throw new Error("Unexpected synthetic test route");
  });
  vi.stubGlobal("fetch", fetch); setSessionToken("t".repeat(43)); const navigate = vi.fn();
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) },
    pending: false, logout: vi.fn(), changePassword: vi.fn() }}><ImportsPage client={httpApiClient} navigate={navigate} /></SessionContext.Provider>);
  return { fetch, state, navigate };
}
async function inspect() {
  fireEvent.change(screen.getByLabelText("Historical source file"), { target: { files: [new File(["synthetic"], "materials.csv")] } });
  fireEvent.change(screen.getByLabelText("CSV delimiter"), { target: { value: ";" } });
  // jsdom's synthetic files property does not populate its native file-input
  // validity state. Exercise the React submit handler here; real uploads and
  // native browser validation are covered by the retained-data Playwright run.
  const button = screen.getByRole("button", { name: "Inspect source" });
  expect(button).toBeEnabled(); fireEvent.submit(button.closest("form")!);
  await screen.findByRole("heading", { name: "2. Map source columns" });
}
async function prepare() {
  await inspect();
  for (const [label, value] of [["Technical identity", "Identity"], ["Material name", "Name"], ["Project label", "Project"], ["Brand label", "Brand"], ["Processor label", "Processor"]])
    fireEvent.change(screen.getByLabelText(label + " column"), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Load source labels" }));
  await screen.findByRole("button", { name: "Prepare import preview" });
  fireEvent.change(screen.getByLabelText("Project: Project A"), { target: { value: materialProject.id } });
  fireEvent.change(screen.getByLabelText("Brand: Brand A"), { target: { value: materialBrand.id } });
  fireEvent.change(screen.getByLabelText("Processor: Processor A"), { target: { value: processorDto.id } });
  fireEvent.click(screen.getByRole("button", { name: "Prepare import preview" }));
  await screen.findByRole("heading", { name: "4. Review and confirm" });
}
function acknowledge() {
  fireEvent.change(screen.getByLabelText("Reason for historical import"), { target: { value: "Reviewed synthetic history" } });
  fireEvent.click(screen.getByRole("checkbox", { name: /I checked the identities/ }));
}
it("requires explicit options, five columns, existing IDs and acknowledgment before creating a batch", async () => {
  const { fetch, navigate } = setup();
  expect(screen.getByRole("button", { name: "Inspect source" })).toBeDisabled();
  await prepare();
  const confirm = screen.getByRole("button", { name: "Confirm import of 1 materials" }); expect(confirm).toBeDisabled();
  expect(fetch.mock.calls.some(([path]) => path.endsWith("/confirm"))).toBe(false);
  const preview = screen.getByRole("article", { name: "Import preview" });
  for (const id of [materialProject.company_id, materialBrand.company_id])
    expect(within(preview).getByText(importCompanies.find((company) => company.id === id)!.name, { selector: "small" })).toBeVisible();
  acknowledge(); fireEvent.click(confirm); await screen.findByRole("heading", { name: "Import completed" });
  const call = fetch.mock.calls.find(([path]) => path.endsWith("/confirm"))!;
  const body = JSON.parse(String(call[1]?.body));
  expect(body.links).toEqual({ projects: { "Project A": materialProject.id }, brands: { "Brand A": materialBrand.id }, processors: { "Processor A": processorDto.id } });
  expect(body.acknowledge_unverified).toBe(true); expect(body.expected_preview_hash).toBe(importPreview.preview_hash);
  fireEvent.click(within(screen.getByRole("article", { name: "Completed import" })).getByRole("link", { name: "LASVIT_0007_G03" }));
  expect(navigate).toHaveBeenCalledWith("/materials/" + importBatch.rows[0].material_id);
});
it("invalidates the reviewed preview after changing a selected record", async () => {
  const { fetch } = setup(); await prepare(); acknowledge();
  fireEvent.change(screen.getByLabelText("Brand: Brand A"), { target: { value: "" } });
  expect(screen.queryByRole("button", { name: "Confirm import of 1 materials" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Prepare import preview" })).toBeDisabled();
  expect(fetch.mock.calls.some(([path]) => path.endsWith("/confirm"))).toBe(false);
});
it("keeps blocked rows visible without offering a partial import", async () => {
  const { state } = setup();
  Object.assign(state.preview, { can_confirm: false, preview_hash: null, findings: [{ row: 2, field: "identity", code: "IMPORT_NUMBER_RESERVED" }] });
  await prepare(); await screen.findByText(/Row 2, identity: This brand number is permanently reserved/);
  expect(screen.queryByLabelText("Reason for historical import")).not.toBeInTheDocument();
});
it.each(["unknown", "mismatch"])("retries the exact original request after an %s outcome", async (failure) => {
  const { state, fetch } = setup(); await prepare(); acknowledge(); state.confirm = failure;
  fireEvent.click(screen.getByRole("button", { name: "Confirm import of 1 materials" }));
  await screen.findByRole("button", { name: "Retry same import confirmation" });
  expect(screen.getByLabelText("Historical source file")).toBeDisabled(); expect(screen.getByLabelText("Reason for historical import")).toBeDisabled();
  const first = fetch.mock.calls.find(([path]) => path.endsWith("/confirm"))!;
  state.confirm = "success"; fireEvent.click(screen.getByRole("button", { name: "Retry same import confirmation" }));
  await screen.findByRole("heading", { name: "Import completed" });
  const calls = fetch.mock.calls.filter(([path]) => path.endsWith("/confirm")); expect(calls).toHaveLength(2); expect(calls[1][1]?.body).toBe(first[1]?.body);
});
it("requires a fresh preview after a definite server rejection", async () => {
  const { state } = setup(); await prepare(); acknowledge(); state.confirm = "rejected";
  fireEvent.click(screen.getByRole("button", { name: "Confirm import of 1 materials" }));
  await screen.findByRole("alert"); expect(screen.queryByRole("button", { name: "Retry same import confirmation" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "4. Review and confirm" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Prepare import preview" })).toBeEnabled();
});
it("requires an explicit XLSX worksheet before exposing column mappings", async () => {
  const { state } = setup();
  fireEvent.change(screen.getByLabelText("Historical source file"), { target: { files: [new File(["synthetic"], "materials.xlsx")] } });
  fireEvent.change(screen.getByLabelText("Source format"), { target: { value: "XLSX" } });
  fireEvent.submit(screen.getByRole("button", { name: "Inspect source" }).closest("form")!);
  await screen.findByLabelText("Worksheet"); expect(screen.queryByLabelText("Technical identity column")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Inspect source" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Worksheet"), { target: { value: "Archive" } });
  fireEvent.submit(screen.getByRole("button", { name: "Inspect source" }).closest("form")!);
  await screen.findByLabelText("Technical identity column"); expect(state.selectedSheet).toBe("Archive");
});
it.each(["PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP"] as const)("makes no import or reference requests for %s", async (role) => {
  const { fetch } = setup(role); expect(screen.getByRole("heading", { name: "Access restricted" })).toBeVisible();
  await waitFor(() => expect(fetch).not.toHaveBeenCalled());
});
