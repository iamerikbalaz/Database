import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AI_BRIEF_MAX_BYTES, aiBriefClient, type AiBrief, type AiBriefReceipt, type AiBriefResults, type AiBriefReviewItem } from "../api/aiBriefClient";
import { ApiError } from "../api/errors";
import { materialFromDto } from "../api/materialDto";
import { materialDto } from "../test/materialFixtures";
import { requestNavigation } from "../navigationGuard";
import { setSessionToken } from "../auth/sessionTransport";
import { MaterialAiBriefDialog } from "./MaterialAiBriefDialog";

const first = { ...materialFromDto(materialDto), materialName: "First material" }, second = { ...first, id: "50000000-0000-4000-8000-000000000002", materialName: "Second material" }, third = { ...first, id: "50000000-0000-4000-8000-000000000003", materialName: "Third material" };
const materials = [first, second, third], batch = "10000000-0000-4000-8000-000000000001";
function result(id = first.id) { return { material_id: id, context_hash: "a".repeat(64), content_revision: 2, description: "A verified textured material.", source_urls: ["https://manufacturer.com/product"], needs_review: false, note: "Based on the product page." }; }
function row(id = first.id): AiBriefReviewItem { return { ...result(id), name: materials.find(material => material.id === id)!.materialName, current_description: null, proposed_description: result(id).description, status: "READY", applicable: true, requires_overwrite: false, result: result(id) }; }
function receipt(id: string): AiBriefReceipt { return { material_id: id, batch_id: batch, draft_id: batch, status: "APPLIED", content_revision: 3, description: result(id).description }; }
function brief(): AiBrief { return { schema_version: "reawote-ai-brief-v1", batch_id: batch, generated_at: first.updatedAt, instructions: "Use the result template.", result_json_schema: { type: "object" }, result_template: { schema_version: "reawote-ai-results-v1", batch_id: batch, items: [result()] }, items: [{ material_id: first.id, context_hash: "a".repeat(64), content_revision: 2, context: { name: first.materialName, customer: { id: batch, name: "Customer", website: null }, categories: [], collections: [], source_urls: [], current_description: null } }], skipped: [] }; }
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  vi.spyOn(aiBriefClient, "generate").mockResolvedValue(brief());
  vi.spyOn(aiBriefClient, "review").mockImplementation(async (results, ids = results.items.map(item => item.material_id)) => ({ batch_id: batch, items: ids.map(id => row(id)) }));
  vi.spyOn(aiBriefClient, "apply").mockImplementation(async id => receipt(id));
  Object.defineProperty(URL, "createObjectURL", { configurable: true, writable: true, value: vi.fn(() => "blob:test-ai-brief") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, writable: true, value: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());
function mount(selected = [first]) { const changed = vi.fn(), close = vi.fn(); return { changed, close, ...render(<MaterialAiBriefDialog materials={selected} onChanged={changed} onClose={close} />) }; }
async function importResults(ids = [first.id], raw?: string) {
  const data: AiBriefResults = { schema_version: "reawote-ai-results-v1", batch_id: batch, items: ids.map(id => result(id)) };
  const file = new File([raw ?? JSON.stringify(data)], "results.json", { type: "application/json" });
  Object.defineProperty(file, "text", { value: async () => raw ?? JSON.stringify(data) });
  fireEvent.change(screen.getByLabelText("Import AI results"), { target: { files: [file] } });
  await waitFor(() => expect(screen.getByLabelText("Import AI results")).toBeEnabled());
}
function select(name = first.materialName) { fireEvent.click(screen.getByLabelText(`I reviewed the description and sources for ${name} and want to apply it.`)); }
function apply(count = 1) { fireEvent.click(screen.getByRole("button", { name: `Apply ${count} reviewed ${count === 1 ? "description" : "descriptions"}` })); }

it("downloads a self-contained JSON brief for the frozen explicit selection and revokes the object URL", async () => {
  const selected = [{ ...first }]; mount(selected); selected[0].id = second.id;
  fireEvent.click(screen.getByRole("button", { name: "Download JSON brief" }));
  await screen.findByText(/JSON brief downloaded for 1 material/);
  expect(aiBriefClient.generate).toHaveBeenCalledExactlyOnceWith([{ id: first.id, expected_updated_at: first.updatedAt }]);
  expect(URL.createObjectURL).toHaveBeenCalledOnce(); expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test-ai-brief");
  expect(aiBriefClient.apply).not.toHaveBeenCalled();
});

it("keeps its padded dialog layout when generic confirmation styles load later", () => {
  const style = document.createElement("style");
  style.textContent = readFileSync("src/components/MaterialAiBriefDialog.css", "utf8") + "\n.confirm-dialog { width: 520px; padding: 0; border: 0; }";
  document.head.append(style);
  try {
    mount(); const computed = getComputedStyle(screen.getByRole("dialog"));
    expect(computed.padding).toBe("24px"); expect(computed.borderTopWidth).toBe("1px");
  } finally { style.remove(); }
});

it("imports for review only, requires explicit row selection, saves an AI draft and refreshes on close", async () => {
  const view = mount(); await importResults();
  expect(screen.getByLabelText(/I reviewed the description/)).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Apply 0 reviewed descriptions" })).toBeDisabled();
  expect(aiBriefClient.apply).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "https://manufacturer.com/product" })).toHaveAttribute("rel", "noopener noreferrer");
  select(); apply(); await screen.findByText("Saved");
  expect(aiBriefClient.apply).toHaveBeenCalledWith(first.id, { idempotency_key: expect.any(String), batch_id: batch, result: result(), overwrite: false });
  expect(view.changed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(view.changed).toHaveBeenCalledOnce(); expect(view.close).toHaveBeenCalledOnce();
});

it("requires overwrite consent and disables stale and uncertain results", async () => {
  vi.mocked(aiBriefClient.review).mockResolvedValue({ batch_id: batch, items: [
    { ...row(), current_description: "Keep until reviewed.", status: "OVERWRITE_REQUIRED", requires_overwrite: true },
    { ...row(second.id), status: "STALE_CONTEXT", applicable: false },
    { ...row(third.id), status: "NEEDS_REVIEW", applicable: false, needs_review: true },
  ] });
  mount(materials); await importResults(materials.map(material => material.id));
  expect(screen.getByLabelText(/I reviewed the description.*First material/)).toBeDisabled();
  expect(screen.getByLabelText(/I reviewed the description.*Second material/)).toBeDisabled();
  expect(screen.getByLabelText(/I reviewed the description.*Third material/)).toBeDisabled();
  const consent = screen.getByLabelText(/I approve replacing/); expect(consent).not.toBeChecked(); fireEvent.click(consent);
  select(); apply(); await screen.findByText("Saved");
  expect(aiBriefClient.apply).toHaveBeenCalledExactlyOnceWith(first.id, expect.objectContaining({ overwrite: true }));
});

it("rejects oversized or invalid files without review or mutation", async () => {
  mount(); const file = new File(["x"], "large.json"); Object.defineProperty(file, "size", { value: AI_BRIEF_MAX_BYTES + 1 });
  fireEvent.change(screen.getByLabelText("Import AI results"), { target: { files: [file] } });
  await screen.findByText(/no larger than 5 MiB/); await importResults([first.id], "not JSON");
  expect(screen.getByRole("alert")).toHaveTextContent("not valid JSON");
  expect(aiBriefClient.review).not.toHaveBeenCalled(); expect(aiBriefClient.apply).not.toHaveBeenCalled();
});

it("clears a previous review before importing another file, including a failed import", async () => {
  mount(); await importResults(); select(); await importResults([first.id], "{}");
  expect(screen.queryByRole("button", { name: /Apply/ })).not.toBeInTheDocument();
  expect(aiBriefClient.apply).not.toHaveBeenCalled();
});

it("stops on an unknown result, retains exact packets through a replay rejection, and skips saved rows", async () => {
  let attempts = 0;
  vi.mocked(aiBriefClient.apply).mockImplementation(async id => {
    if (id === second.id && ++attempts <= 2) throw attempts === 1 ? new TypeError("Lost response") : new ApiError(403, "Permission changed");
    return receipt(id);
  });
  mount(materials); await importResults(materials.map(material => material.id)); materials.forEach(material => select(material.materialName)); apply(3);
  const retry = await screen.findByRole("button", { name: "Retry same save and continue" });
  expect(aiBriefClient.apply).toHaveBeenCalledTimes(2); expect(screen.getByRole("button", { name: "Close" })).toBeDisabled(); expect(requestNavigation("/orders")).toBe(false);
  fireEvent.click(retry); await waitFor(() => expect(aiBriefClient.apply).toHaveBeenCalledTimes(3));
  await waitFor(() => expect(retry).toBeEnabled()); fireEvent.click(retry);
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("3 saved"));
  expect(aiBriefClient.apply).toHaveBeenCalledTimes(5);
  const calls = vi.mocked(aiBriefClient.apply).mock.calls.filter(([id]) => id === second.id);
  expect(calls[1]).toEqual(calls[0]); expect(calls[2]).toEqual(calls[0]);
  expect(vi.mocked(aiBriefClient.apply).mock.calls.filter(([id]) => id === first.id)).toHaveLength(1);
  expect(requestNavigation("/orders")).toBe(true);
});

it("reports definitive conflicts per material and continues remaining reviewed rows", async () => {
  const error = new ApiError(409, "Conflict"); error.code = "AI_CONTEXT_CHANGED";
  vi.mocked(aiBriefClient.apply).mockRejectedValueOnce(error).mockResolvedValueOnce(receipt(second.id));
  mount([first, second]); await importResults([first.id, second.id]); select(); select(second.materialName); apply(2);
  await screen.findByText("Saved");
  expect(within(screen.getByRole("article", { name: first.materialName })).getByText("Rejected")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("1 saved · 1 rejected");
  expect(screen.getByRole("button", { name: "Close" })).toBeEnabled();
});

it("does not send the next save after unmount", async () => {
  let resolve!: (value: AiBriefReceipt) => void;
  vi.mocked(aiBriefClient.apply).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const view = mount([first, second]); await importResults([first.id, second.id]); select(); select(second.materialName); apply(2);
  await waitFor(() => expect(aiBriefClient.apply).toHaveBeenCalledOnce()); view.unmount();
  await act(async () => { resolve(receipt(first.id)); });
  expect(aiBriefClient.apply).toHaveBeenCalledOnce(); expect(view.changed).not.toHaveBeenCalled();
});

it("retains an unknown first save and stops remaining rows when the session changes", async () => {
  let resolve!: (value: AiBriefReceipt) => void;
  vi.mocked(aiBriefClient.apply).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  mount([first, second]); await importResults([first.id, second.id]); select(); select(second.materialName); apply(2);
  await waitFor(() => expect(aiBriefClient.apply).toHaveBeenCalledOnce());
  await act(async () => { setSessionToken(null); resolve(receipt(first.id)); });
  expect(aiBriefClient.apply).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "Retry same save and continue" })).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("session changed");
});

it("rejects empty, duplicate or oversized selections before exporting", () => {
  const view = mount([]); expect(screen.getByRole("button", { name: "Download JSON brief" })).toBeDisabled(); view.unmount();
  const duplicate = mount([first, first]); expect(screen.getByRole("button", { name: "Download JSON brief" })).toBeDisabled(); duplicate.unmount();
  mount(Array.from({ length: 101 }, () => first)); expect(screen.getByRole("button", { name: "Download JSON brief" })).toBeDisabled();
  expect(aiBriefClient.generate).not.toHaveBeenCalled();
});

it("imports a self-contained file after closing and reopening without a material selection", async () => {
  const old = mount(); await importResults(); old.unmount();
  render(<MaterialAiBriefDialog onChanged={vi.fn()} onClose={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Download JSON brief" })).not.toBeInTheDocument();
  await importResults([first.id, second.id]);
  expect(aiBriefClient.review).toHaveBeenLastCalledWith(expect.objectContaining({ batch_id: batch }), [first.id, second.id]);
  expect(screen.getByRole("button", { name: "Accept all 2 changes" })).toBeEnabled();
  expect(aiBriefClient.apply).not.toHaveBeenCalled();
});

it("uses matching buttons for upload and download and shows proposed tags without removing existing tags", async () => {
  vi.mocked(aiBriefClient.review).mockResolvedValue({ batch_id: batch, items: [{ ...row(), current_tags: ["existing"], proposed_tags: ["matte", "ceramic"], merged_tags: ["ceramic", "existing", "matte"] }] });
  mount(); await importResults();
  expect(screen.getByRole("button", { name: "Import AI results" }).className).toBe(screen.getByRole("button", { name: "Download JSON brief" }).className);
  expect(screen.getByText("matte, ceramic")).toBeInTheDocument(); expect(screen.getByText("existing")).toBeInTheDocument();
});

it("accepts all eligible proposals in one explicit action including described replacements, and skips blocked rows", async () => {
  vi.mocked(aiBriefClient.review).mockResolvedValue({ batch_id: batch, items: [
    { ...row(), current_description: "Old description", requires_overwrite: true, status: "OVERWRITE_REQUIRED" },
    row(second.id), { ...row(third.id), applicable: false, status: "STALE_CONTEXT" },
  ] });
  mount(materials); await importResults(materials.map(item => item.id));
  fireEvent.click(screen.getByRole("button", { name: "Accept all 2 changes (replace 1 existing description)" }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("2 saved"));
  expect(aiBriefClient.apply).toHaveBeenCalledTimes(2);
  expect(aiBriefClient.apply).toHaveBeenCalledWith(first.id, expect.objectContaining({ overwrite: true }));
  expect(aiBriefClient.apply).toHaveBeenCalledWith(second.id, expect.objectContaining({ overwrite: false }));
});

it("resumes a partially saved file after restart without applying completed or unavailable rows again", async () => {
  vi.mocked(aiBriefClient.review).mockResolvedValue({ batch_id: batch, items: [
    { ...row(), status: "ALREADY_APPLIED", applicable: false }, row(second.id),
    { ...row(third.id), status: "MATERIAL_UNAVAILABLE", applicable: false },
  ] });
  render(<MaterialAiBriefDialog onChanged={vi.fn()} onClose={vi.fn()} />);
  await importResults(materials.map(item => item.id));
  expect(screen.getByText(/Already applied from this JSON file/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Accept all 1 changes" }));
  await screen.findByText("Saved");
  expect(aiBriefClient.apply).toHaveBeenCalledExactlyOnceWith(second.id, expect.any(Object));
});
