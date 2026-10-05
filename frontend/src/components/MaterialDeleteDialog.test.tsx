import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialDeleteDialog } from "./MaterialDeleteDialog";
import { materialDeletionClient, type MaterialDeletionMode, type MaterialDeletionPlan, type MaterialDeletionResult } from "../api/materialDeletionClient";
import { materialFromDto, type Material } from "../api/materialDto";
import { materialDto, processorDto } from "../test/materialFixtures";
import { SessionContext } from "../auth/context";
import { requestNavigation } from "../navigationGuard";
import { ApiError } from "../api/errors";

const first = materialFromDto(materialDto);
const second = { ...first, id: "50000000-0000-4000-8000-000000000002", materialName: "SECOND" };
const operationId = "50000000-0000-4000-8000-000000000099";
function plan(materials = [first], mode: MaterialDeletionMode = "RECORD_ONLY"): MaterialDeletionPlan {
  return { proposal_hash: "a".repeat(64), mode, can_apply: true, total: materials.length, warnings: [], items: materials.map(item => ({
    material_id: item.id, material_name: item.materialName, identity: item.technicalIdentity, folder_path: item.folderPath, file_count: null, issues: [],
  })) };
}
function receipt(materials = [first], status: MaterialDeletionResult["status"] = "COMPLETED", mode: MaterialDeletionMode = "RECORD_ONLY"): MaterialDeletionResult {
  return { id: operationId, mode, status, deleted_count: status === "COMPLETED" ? materials.length : status === "PARTIAL" ? 1 : 0, quarantine_retained: mode === "RECORD_AND_FILES",
    items: materials.map((item, index) => ({ material_id: item.id, material_name: item.materialName, identity: item.technicalIdentity,
      status: status === "PARTIAL" ? index === 0 ? "COMPLETED" : "REJECTED" : status, error_code: status === "PARTIAL" && index > 0 ? "MATERIAL_DELETE_CHANGED" : null })) };
}
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  vi.spyOn(materialDeletionClient, "pending").mockResolvedValue([]);
  vi.spyOn(materialDeletionClient, "plan").mockResolvedValue(plan());
  vi.spyOn(materialDeletionClient, "apply").mockResolvedValue(receipt());
  vi.spyOn(materialDeletionClient, "resume").mockResolvedValue(receipt());
});
afterEach(() => vi.restoreAllMocks());
function mount(materials: Material[] = [first], role: "ADMIN" | "PROCESSOR" = "ADMIN") {
  const close = vi.fn(), finished = vi.fn();
  const view = render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialDeleteDialog materials={materials} onClose={close} onFinished={finished} />
  </SessionContext.Provider>);
  return { ...view, close, finished };
}
async function review() {
  await screen.findByRole("region", { name: "Reviewed deletion plan" });
}
async function confirm() {
  await waitFor(() => expect(screen.getByRole("button", { name: "Delete materials" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Delete materials" }));
}

it("loads the review automatically, freezes IDs/revisions, confirms once, and refreshes on Close", async () => {
  const selection = [{ ...first }]; const view = mount(selection);
  expect(screen.getByRole("button", { name: "Delete materials" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Review deletion" })).not.toBeInTheDocument();
  selection[0].updatedAt = "changed outside dialog"; selection.push(second);
  await review();
  expect(materialDeletionClient.plan).toHaveBeenCalledWith({ mode: "RECORD_ONLY", materials: [{ id: first.id, expected_updated_at: first.updatedAt }] });
  await waitFor(() => expect(screen.getByRole("button", { name: "Delete materials" })).toBeEnabled());
  expect(screen.queryByRole("checkbox", { name: /I reviewed these materials/ })).not.toBeInTheDocument();
  expect(materialDeletionClient.apply).not.toHaveBeenCalled();
  await confirm(); await screen.findByText("1 deleted · Completed");
  expect(materialDeletionClient.apply).toHaveBeenCalledWith({ mode: "RECORD_ONLY", materials: [{ id: first.id, expected_updated_at: first.updatedAt }], expected_proposal_hash: "a".repeat(64), confirmed: true, idempotency_key: expect.any(String) });
  expect(view.finished).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(view.finished).toHaveBeenCalledOnce(); expect(view.close).toHaveBeenCalledOnce();
});

it("explains recoverable quarantine and invalidates review when the deletion mode changes", async () => {
  mount(); await review();
  vi.mocked(materialDeletionClient.plan).mockResolvedValue(plan([first], "RECORD_AND_FILES"));
  vi.mocked(materialDeletionClient.apply).mockResolvedValue(receipt([first], "COMPLETED", "RECORD_AND_FILES"));
  fireEvent.click(screen.getByRole("radio", { name: /Records and source folders/ }));
  expect(screen.getByRole("button", { name: "Delete materials" })).toBeDisabled();
  expect(screen.getByText(/protected recovery quarantine/)).toBeInTheDocument();
  expect(screen.getByText(/Google Cloud Storage stay unchanged/)).toBeInTheDocument();
  await review(); await confirm(); await screen.findByText("1 deleted · Completed");
  expect(materialDeletionClient.apply).toHaveBeenCalledWith(expect.objectContaining({ mode: "RECORD_AND_FILES" }));
});

it("ignores an older plan after switching modes twice and confirms only the latest proposal", async () => {
  let finishOld!: (value: MaterialDeletionPlan) => void;
  vi.mocked(materialDeletionClient.plan)
    .mockReturnValueOnce(new Promise(resolve => { finishOld = resolve; }))
    .mockResolvedValueOnce(plan([first], "RECORD_AND_FILES"))
    .mockResolvedValueOnce({ ...plan(), proposal_hash: "b".repeat(64) });
  mount();
  await waitFor(() => expect(materialDeletionClient.plan).toHaveBeenCalledOnce());
  expect(screen.getByRole("button", { name: "Delete materials" })).toBeDisabled();
  fireEvent.click(screen.getByRole("radio", { name: /Records and source folders/ }));
  await review();
  fireEvent.click(screen.getByRole("radio", { name: /Records only/ }));
  await waitFor(() => expect(materialDeletionClient.plan).toHaveBeenCalledTimes(3));
  await review();
  await act(async () => finishOld(plan()));
  await confirm();
  await screen.findByText("1 deleted · Completed");
  expect(materialDeletionClient.apply).toHaveBeenCalledWith(expect.objectContaining({ mode: "RECORD_ONLY", expected_proposal_hash: "b".repeat(64) }));
});

it("ignores a previous mode's failed check after the current plan is ready", async () => {
  let rejectOld!: (reason: Error) => void;
  vi.mocked(materialDeletionClient.plan)
    .mockReturnValueOnce(new Promise((_, reject) => { rejectOld = reject; }))
    .mockResolvedValueOnce(plan([first], "RECORD_AND_FILES"));
  mount();
  await waitFor(() => expect(materialDeletionClient.plan).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("radio", { name: /Records and source folders/ }));
  await review();
  await act(async () => rejectOld(new Error("Old mode failed")));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Delete materials" })).toBeEnabled();
});

it("allows closing while a read-only plan is loading and discards its response", async () => {
  let finishPlan!: (value: MaterialDeletionPlan) => void;
  vi.mocked(materialDeletionClient.plan).mockReturnValue(new Promise(resolve => { finishPlan = resolve; }));
  const view = mount();
  await waitFor(() => expect(materialDeletionClient.plan).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(view.close).toHaveBeenCalledOnce();
  view.unmount();
  await act(async () => finishPlan(plan()));
  expect(materialDeletionClient.apply).not.toHaveBeenCalled();
  expect(requestNavigation("/orders")).toBe(true);
});

it("keeps deletion disabled until a failed preview has been checked successfully", async () => {
  vi.mocked(materialDeletionClient.plan).mockRejectedValueOnce(new Error("Source unavailable")).mockResolvedValueOnce(plan());
  mount();
  await screen.findByText("Source unavailable");
  expect(screen.getByRole("button", { name: "Delete materials" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry checks" }));
  await review();
  await waitFor(() => expect(screen.getByRole("button", { name: "Delete materials" })).toBeEnabled());
  expect(materialDeletionClient.apply).not.toHaveBeenCalled();
});

it("shows readable material cards and plain descriptions for warning codes", async () => {
  const value = plan();
  value.warnings = ["RECORDS_REMOVED_FROM_ALL_VIEWS", "AUDIT_RETAINED", "PUBLISHED_EXTERNAL_FILES_UNCHANGED", "SOURCE_FILES_UNCHANGED"];
  vi.mocked(materialDeletionClient.plan).mockResolvedValue(value);
  mount(); await review();
  expect(screen.getByRole("list", { name: "Selected materials" })).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.getByText("Deletion history is retained.")).toBeInTheDocument();
  expect(screen.getByText("Source folders and files stay in their current location.")).toBeInTheDocument();
  for (const code of value.warnings) expect(screen.queryByText(code)).not.toBeInTheDocument();
  expect(screen.getByRole("dialog")).not.toHaveClass("confirm-dialog");
});

it("shows blocked plans without submission and permits closing after preflight failures", async () => {
  vi.mocked(materialDeletionClient.plan).mockRejectedValue(new Error("Material changed. Refresh selection."));
  const view = mount();
  await screen.findByText("Material changed. Refresh selection.");
  expect(materialDeletionClient.apply).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" })); expect(view.close).toHaveBeenCalledOnce();
});

it("blocks a stale server plan with a different material selection", async () => {
  vi.mocked(materialDeletionClient.plan).mockResolvedValue(plan([second])); mount();
  await screen.findByText(/returned plan does not match/);
  expect(materialDeletionClient.apply).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
});

it("reports a known stale confirmation rejection and unlocks closing without pretending deletion succeeded", async () => {
  const error = new ApiError(409, "Stale"); error.code = "MATERIAL_DELETE_PLAN_CHANGED";
  vi.mocked(materialDeletionClient.apply).mockRejectedValue(error);
  const view = mount(); await review(); await confirm();
  await screen.findByText(/rejected before changes/);
  expect(requestNavigation("/orders")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(view.close).toHaveBeenCalledOnce(); expect(view.finished).not.toHaveBeenCalled();
});

it("retains the exact packet across unknown response and 403, locks mode/close and safely retries", async () => {
  vi.mocked(materialDeletionClient.apply).mockRejectedValueOnce(new TypeError("Lost response")).mockRejectedValueOnce(new ApiError(403, "Permission changed")).mockResolvedValue(receipt());
  mount(); await review(); await confirm();
  const retry = await screen.findByRole("button", { name: "Recover same deletion" });
  await waitFor(() => expect(retry).toBeEnabled());
  expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
  expect(screen.getByRole("radio", { name: /Records and source folders/ })).toBeDisabled();
  expect(requestNavigation("/orders")).toBe(false);
  fireEvent.click(retry); await waitFor(() => expect(materialDeletionClient.apply).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(retry).toBeEnabled()); fireEvent.click(retry);
  await screen.findByText("1 deleted · Completed");
  const calls = vi.mocked(materialDeletionClient.apply).mock.calls;
  expect(calls[1]).toEqual(calls[0]); expect(calls[2]).toEqual(calls[0]);
  expect(materialDeletionClient.plan).toHaveBeenCalledOnce(); expect(requestNavigation("/orders")).toBe(true);
});

it("keeps a mismatched receipt unknown, never allowing a fresh deletion", async () => {
  vi.mocked(materialDeletionClient.apply).mockResolvedValue(receipt([second])); mount(); await review(); await confirm();
  await screen.findByText(/result is unknown/);
  expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Review deletion" })).not.toBeInTheDocument();
});

it("retains the initial packet on MATERIAL_DELETE_CHANGED because earlier items may already be deleted", async () => {
  const error = new ApiError(409, "Changed during finishing"); error.code = "MATERIAL_DELETE_CHANGED";
  vi.mocked(materialDeletionClient.apply).mockRejectedValueOnce(error).mockResolvedValue(receipt());
  mount(); await review(); await confirm();
  const button = await screen.findByRole("button", { name: "Recover same deletion" });
  await waitFor(() => expect(button).toBeEnabled());
  expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
  fireEvent.click(button); await screen.findByText("1 deleted · Completed");
  const calls = vi.mocked(materialDeletionClient.apply).mock.calls;
  expect(calls[1]).toEqual(calls[0]);
});

it("shows issues in a blocked plan and disables deletion", async () => {
  const blocked = plan(); blocked.can_apply = false;
  blocked.items[0].issues = [{ code: "FOLDER_MISSING", message: "Source folder is unavailable." }];
  vi.mocked(materialDeletionClient.plan).mockResolvedValue(blocked);
  mount(); await review();
  expect(screen.getByText("Source folder is unavailable.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Delete materials" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
});

it("reports terminal partial failure without repeating completed materials", async () => {
  vi.mocked(materialDeletionClient.plan).mockResolvedValue(plan([first, second]));
  vi.mocked(materialDeletionClient.apply).mockResolvedValue(receipt([first, second], "PARTIAL"));
  const view = mount([first, second]); await review(); await confirm();
  await screen.findByText("1 deleted · Partly completed");
  expect(screen.getByText(/Not deleted · This material changed/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Recover/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(view.finished).toHaveBeenCalledOnce(); expect(materialDeletionClient.apply).toHaveBeenCalledOnce();
});

it("discovers and explicitly reviews the full stored operation, including materials outside selection", async () => {
  vi.mocked(materialDeletionClient.pending).mockResolvedValue([receipt([first, second], "RECOVERY_REQUIRED", "RECORD_AND_FILES")]);
  vi.mocked(materialDeletionClient.resume).mockResolvedValue(receipt([first, second], "COMPLETED", "RECORD_AND_FILES"));
  const view = mount([first]);
  const button = await screen.findByRole("button", { name: "Recover recorded deletion" });
  expect(button).toBeDisabled(); expect(screen.getByText("SECOND")).toBeInTheDocument();
  expect(screen.getByText(/including any materials outside/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: /I reviewed all recorded/ })); fireEvent.click(button);
  await screen.findByText("2 deleted · Completed");
  expect(materialDeletionClient.resume).toHaveBeenCalledWith(operationId);
  expect(materialDeletionClient.plan).not.toHaveBeenCalled(); expect(materialDeletionClient.apply).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Close" })); expect(view.finished).toHaveBeenCalledOnce();
});

it("resumes a known recovery operation after a failed response without constructing a new packet", async () => {
  vi.mocked(materialDeletionClient.apply).mockResolvedValue(receipt([first], "RECOVERY_REQUIRED"));
  vi.mocked(materialDeletionClient.resume).mockRejectedValueOnce(new TypeError("Lost")).mockResolvedValue(receipt());
  mount(); await review(); await confirm();
  const button = await screen.findByRole("button", { name: "Recover recorded deletion" });
  fireEvent.click(screen.getByRole("checkbox", { name: /I reviewed all recorded/ })); fireEvent.click(button);
  const retry = await screen.findByRole("button", { name: "Recover same deletion" });
  await waitFor(() => expect(retry).toBeEnabled()); fireEvent.click(retry);
  await screen.findByText("1 deleted · Completed");
  expect(materialDeletionClient.apply).toHaveBeenCalledOnce();
  expect(vi.mocked(materialDeletionClient.resume).mock.calls).toEqual([[operationId], [operationId]]);
});

it("does not query or mutate as a non-admin", () => {
  mount([first], "PROCESSOR"); expect(screen.getByRole("alert")).toHaveTextContent("Only administrators");
  expect(screen.getByRole("button", { name: "Delete materials" })).toBeDisabled();
  expect(materialDeletionClient.pending).not.toHaveBeenCalled(); expect(materialDeletionClient.plan).not.toHaveBeenCalled();
});

it("rejects an oversized or duplicate selection before querying operations", () => {
  mount([first, first]); expect(screen.getByRole("alert")).toHaveTextContent("between 1 and 100 distinct");
  expect(materialDeletionClient.pending).not.toHaveBeenCalled();
});
