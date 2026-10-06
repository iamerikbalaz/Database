import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { httpApiClient } from "../api/client";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";
import { setSessionToken } from "../auth/sessionTransport";
import { materialBrand, materialProject, processorDto } from "../test/materialFixtures";
import { importBatch, importCompanies, importInspection, importMappings, importPreview } from "../test/importFixtures";
import { ImportsPage } from "./ImportsPage";
import { MaterialAiBriefDialog } from "../components/MaterialAiBriefDialog";
import { CustomerAiBriefDialog } from "../components/CustomerAiBriefDialog";
import importTemplateUrl from "../assets/material-import-template.csv?url&no-inline";

vi.mock("../components/MaterialAiBriefDialog", () => ({ MaterialAiBriefDialog: vi.fn(({ onClose, onChanged }) =>
  <div role="dialog" aria-label="Standalone AI results"><button onClick={() => { onChanged(); onClose(); }}>Save reviewed results</button></div>) }));
vi.mock("../components/CustomerAiBriefDialog", () => ({ CustomerAiBriefDialog: vi.fn(({ onClose, onChanged }) =>
  <div role="dialog" aria-label="Standalone Customer AI results"><button onClick={() => { onChanged(); onClose(); }}>Save reviewed customer results</button></div>) }));

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
function setup(role: Role = "ADMIN") {
  const state = { preview: structuredClone(importPreview), batch: structuredClone(importBatch), confirm: "success", completed: false, inspections: 0, format: "CSV", selectedSheet: "" };
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.endsWith("/orders")) return json([{ ...materialProject, notion_page_id: null, number: materialProject.project_number,
      customer_id: materialBrand.id, project_type: null, starting_date: null, due_date: null, notes: null, responsible_id: null,
      status: "Ongoing", priority: null, generated_name: materialProject.name, folder_path: null }]);
    if (path.endsWith("/customers")) return json([{ ...materialBrand, notion_page_id: null, status: "Active cooperation", website: null,
      address: null, shipping_address: null, legal_name: null, vat_id: null, description: null, notes: null,
      main_category_codes: [], has_logo: false, legacy_company_id: materialBrand.company_id }]);
    if (path.includes("/internal-users")) return json([processorDto]);
    if (path.includes("/material-imports?")) return json({ items: state.completed ? [importBatch] : [], next_after: null });
    if (path.endsWith("/" + importBatch.id)) return json(importBatch);
    const body = JSON.parse(String(init?.body ?? "{}"));
    if (path.endsWith("/inspect")) {
      state.inspections++; state.format = body.source.format; state.selectedSheet = body.source.sheet ?? "";
      if (body.source.format === "XLSX" && !body.source.sheet) return json({ format: "XLSX", sheets: ["Materials", "Archive"], requires_sheet: true });
      return json({ ...importInspection, format: body.source.format, sheets: body.source.format === "XLSX" ? ["Materials", "Archive"] : [],
        ...(body.columns ? { mapping_values: { ...importMappings, project: body.columns.project === null ? [] : importMappings.project } } : {}) });
    }
    if (path.endsWith("/preview")) return json(state.preview);
    if (path.endsWith("/confirm")) {
      if (state.confirm === "unknown") throw new TypeError("Synthetic lost response");
      if (state.confirm === "forbidden") return json({ detail: "Forbidden" }, 403);
      if (state.confirm === "rejected") return json({ detail: { code: "IMPORT_PREVIEW_CHANGED" } }, 409);
      state.completed = true;
      return json({ ...state.batch, idempotency_key: body.idempotency_key, preview_hash: body.expected_preview_hash,
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
  for (const [label, value] of [["Technical identity", "Identity"], ["Material name", "Name"], ["Order label", "Project"], ["Customer label", "Brand"], ["Processor label", "Processor"]])
    fireEvent.change(screen.getByLabelText(label + " column"), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Load source labels" }));
  await screen.findByRole("button", { name: "Prepare import preview" });
  fireEvent.change(screen.getByLabelText("Order: Project A"), { target: { value: materialProject.id } });
  fireEvent.change(screen.getByLabelText("Customer: Brand A"), { target: { value: materialBrand.id } });
  fireEvent.change(screen.getByLabelText("Processor: Processor A"), { target: { value: processorDto.id } });
  fireEvent.click(screen.getByRole("button", { name: "Prepare import preview" }));
  await screen.findByRole("heading", { name: "4. Review and confirm" });
}
function acknowledge() {
  fireEvent.change(screen.getByLabelText("Reason for historical import"), { target: { value: "Reviewed synthetic history" } });
  fireEvent.click(screen.getByRole("checkbox", { name: /I checked the identities/ }));
}
it("explicitly omits historical project mapping instead of inventing a project", async () => {
  const { fetch } = setup();
  await inspect();
  fireEvent.click(screen.getByRole("checkbox", { name: "Import all rows without an Order column" }));
  for (const [label, value] of [["Technical identity", "Identity"], ["Material name", "Name"], ["Customer label", "Brand"], ["Processor label", "Processor"]])
    fireEvent.change(screen.getByLabelText(label + " column"), { target: { value } });
  expect(screen.queryByLabelText("Order label column")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Load source labels" }));
  await screen.findByRole("button", { name: "Prepare import preview" });
  expect(screen.queryByLabelText("Order: Project A")).not.toBeInTheDocument();
  const request = fetch.mock.calls.filter(([path]) => path.endsWith("/inspect")).at(-1)!;
  expect(JSON.parse(String(request[1]?.body)).columns.project).toBeNull();
});
it("requires explicit options, five columns, existing IDs and acknowledgment before creating a batch", async () => {
  const { fetch, navigate } = setup();
  expect(screen.getByRole("button", { name: "Inspect source" })).toBeDisabled();
  await prepare();
  const confirm = screen.getByRole("button", { name: "Confirm import of 1 materials" }); expect(confirm).toBeDisabled();
  expect(fetch.mock.calls.some(([path]) => path.endsWith("/confirm"))).toBe(false);
  const preview = screen.getByRole("article", { name: "Import preview" });
  expect(within(preview).getByRole("columnheader", { name: "Order" })).toBeVisible();
  expect(within(preview).getByRole("columnheader", { name: "Customer" })).toBeVisible();
  for (const company of importCompanies) expect(within(preview).queryByText(company.name, { selector: "small" })).not.toBeInTheDocument();
  expect(fetch.mock.calls.some(([path]) => /\/(companies|projects|brands)$/.test(path))).toBe(false);
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
  fireEvent.change(screen.getByLabelText("Customer: Brand A"), { target: { value: "" } });
  expect(screen.queryByRole("button", { name: "Confirm import of 1 materials" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Prepare import preview" })).toBeDisabled();
  expect(fetch.mock.calls.some(([path]) => path.endsWith("/confirm"))).toBe(false);
});
it("keeps blocked rows visible without offering a partial import", async () => {
  const { state } = setup();
  Object.assign(state.preview, { can_confirm: false, preview_hash: null, findings: [{ row: 2, field: "identity", code: "IMPORT_NUMBER_RESERVED" }] });
  await prepare(); await screen.findByText(/Row 2, identity: This Customer number is permanently reserved/);
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

it("retains an unknown confirmation after a later 403 and recovers the exact original packet", async () => {
  const { state, fetch } = setup(); await prepare(); acknowledge(); state.confirm = "unknown";
  fireEvent.click(screen.getByRole("button", { name: "Confirm import of 1 materials" }));
  await screen.findByRole("button", { name: "Retry same import confirmation" });
  const first = fetch.mock.calls.find(([path]) => path.endsWith("/confirm"))!;
  state.confirm = "forbidden";
  fireEvent.click(screen.getByRole("button", { name: "Retry same import confirmation" }));
  await waitFor(() => expect(fetch.mock.calls.filter(([path]) => path.endsWith("/confirm"))).toHaveLength(2));
  await waitFor(() => expect(screen.getByRole("button", { name: "Retry same import confirmation" })).toBeEnabled());
  expect(screen.getByLabelText("Historical source file")).toBeDisabled();
  expect(screen.getByLabelText("Reason for historical import")).toBeDisabled();
  expect(screen.getByRole("heading", { name: "4. Review and confirm" })).toBeVisible();
  state.confirm = "success";
  fireEvent.click(screen.getByRole("button", { name: "Retry same import confirmation" }));
  await screen.findByRole("heading", { name: "Import completed" });
  const calls = fetch.mock.calls.filter(([path]) => path.endsWith("/confirm"));
  expect(calls).toHaveLength(3);
  for (const call of calls) expect(call[1]?.body).toBe(first[1]?.body);
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
it.each(["PROCESSOR", "LEADERSHIP"] as const)("makes no import or reference requests for %s", async (role) => {
  const { fetch } = setup(role); expect(screen.getByRole("heading", { name: "Access restricted" })).toBeVisible();
  await waitFor(() => expect(fetch).not.toHaveBeenCalled());
});

it("offers a downloadable UTF-8 semicolon template with explicit mapping help", () => {
  setup();
  const link = screen.getByRole("link", { name: "Download CSV template" });
  expect(link).toHaveAttribute("href", importTemplateUrl);
  expect(importTemplateUrl).toContain("material-import-template.csv");
  expect(importTemplateUrl).not.toBe("/material-import-template.csv");
  expect(link).toHaveAttribute("download", "material-import-template.csv");
  expect(screen.getByText(/The template is UTF-8 CSV with a semicolon/)).toBeVisible();
});

it("allows production leads to review standalone AI JSON without accessing historical CSV", async () => {
  const { fetch } = setup("PRODUCTION_LEAD");
  expect(screen.queryByLabelText("Historical source file")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Upload Material AI results JSON" }));
  expect(screen.getByRole("dialog", { name: "Standalone AI results" })).toBeVisible();
  expect(vi.mocked(MaterialAiBriefDialog).mock.calls.at(-1)?.[0].materials).toBeUndefined();
  fireEvent.click(screen.getByRole("button", { name: "Save reviewed results" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Reviewed AI results saved");
  await waitFor(() => expect(fetch).not.toHaveBeenCalled());
});

it("separates Customer and Material JSON imports and derives Customer IDs from the standalone file", async () => {
  const { fetch } = setup("PRODUCTION_LEAD");
  expect(screen.getByRole("heading", { name: "Import Material AI results" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Import Customer AI results" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Upload Customer AI results JSON" }));
  expect(screen.getByRole("dialog", { name: "Standalone Customer AI results" })).toBeVisible();
  expect(vi.mocked(CustomerAiBriefDialog).mock.calls.at(-1)?.[0].customers).toBeUndefined();
  fireEvent.click(screen.getByRole("button", { name: "Save reviewed customer results" }));
  expect(screen.getByRole("status")).toHaveTextContent("saved to Customer profiles");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await waitFor(() => expect(fetch).not.toHaveBeenCalled());
});

it.each([false, true])("compares reviewed spreadsheet properties by value and rejects changed values (changed=%s)", async (changed) => {
  const { state } = setup();
  const properties = { hex_color: "#AABBCC", workflow_status: "DONE", checked_status: "OK", note: "Reviewed note" };
  Object.assign(state.preview.snapshot, { schema_version: 2, rows: state.preview.snapshot.rows.map(row => ({ ...row, properties })) });
  Object.assign(state.batch.snapshot, { schema_version: 2 });
  Object.assign(state.batch, { rows: state.batch.rows.map(row => ({ ...row, workflow_status: "DONE", checked_status: "OK",
    properties: { ...properties, hex_color: changed ? "#000000" : "#AABBCC" } })) });
  await prepare(); acknowledge(); fireEvent.click(screen.getByRole("button", { name: "Confirm import of 1 materials" }));
  if (changed) await screen.findByRole("button", { name: "Retry same import confirmation" });
  else await screen.findByRole("heading", { name: "Import completed" });
});
