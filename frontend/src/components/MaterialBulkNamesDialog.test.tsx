import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialBulkNamesDialog } from "./MaterialBulkNamesDialog";
import { identityClient, type IdentityOperation, type IdentityPlan } from "../api/identityClient";
import { mockApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import { materialFromDto, type Material } from "../api/materialDto";
import { materialDto } from "../test/materialFixtures";
import { requestNavigation } from "../navigationGuard";

const first: Material = { ...materialFromDto(materialDto), materialName: "OLD-OLD", folderPath: "CUSTOMER/CUSTOMER_0001_OLD-OLD_G03",
  technicalIdentity: "CUSTOMER_0001_OLD-OLD_G03", sequenceNumber: 1 };
const second = { ...first, id: "50000000-0000-4000-8000-000000000002", sequenceNumber: 2, technicalIdentity: "CUSTOMER_0002_OLD-OLD_G03", folderPath: "CUSTOMER/CUSTOMER_0002_OLD-OLD_G03" };
const third = { ...first, id: "50000000-0000-4000-8000-000000000003", sequenceNumber: 3, technicalIdentity: "CUSTOMER_0003_OLD-OLD_G03", folderPath: "CUSTOMER/CUSTOMER_0003_OLD-OLD_G03" };
const fixtures = [first, second, third];
function context(material: Material) {
  return { materialId: material.id, identity: material.technicalIdentity, folder: material.folderPath, brandId: material.publishedBrandId,
    category: material.mainCategoryCode, number: material.sequenceNumber, name: material.materialName };
}
function proposal(material: Material, requested = "NEW-NEW"): IdentityPlan {
  const name = requested.replaceAll(" ", "-");
  return { source: context(material), target: { ...context(material), name, identity: material.technicalIdentity.replace("OLD-OLD", name), folder: material.folderPath!.replace("OLD-OLD", name) },
    generation: 3, hash: "a".repeat(64), reservesNumber: false, workerHash: "b".repeat(64), sourceHash: "c".repeat(64), ready: true,
    errors: [], warnings: [], metadata: { beforeHash: null, afterHash: "d".repeat(64), fields: ["material_name"] }, changes: [] };
}
function operation(material = first, status = "COMPLETED"): IdentityOperation {
  const plan = proposal(material);
  return { id: "00000000-0000-4000-8000-" + String(material.sequenceNumber).padStart(12, "0"), status, source: plan.source, target: plan.target,
    reason: "Bulk material name replacement", createdAt: material.updatedAt, actorId: "40000000-0000-4000-8000-000000000001", failure: null };
}
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  vi.spyOn(identityClient, "operations").mockResolvedValue({ enabled: true, items: [] });
  vi.spyOn(identityClient, "plan").mockImplementation(async (id, target) => proposal(fixtures.find(material => material.id === id)!, target.material_name));
  vi.spyOn(identityClient, "confirm").mockImplementation(async id => operation(fixtures.find(material => material.id === id)!));
  vi.spyOn(identityClient, "resume").mockImplementation(async id => operation(fixtures.find(material => material.id === id)!));
});
afterEach(() => vi.restoreAllMocks());
function mount(materials = [first]) {
  const client = { ...mockApiClient, getMaterial: vi.fn(async (id: string) => materials.find(material => material.id === id)!) };
  const close = vi.fn(), changed = vi.fn();
  const view = render(<MaterialBulkNamesDialog materials={materials} client={client} onClose={close} onChanged={changed} />);
  return { client, close, changed, ...view };
}
async function review(find = "old", replacement = "new") {
  await waitFor(() => expect(screen.getByLabelText("Replace text")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Replace text"), { target: { value: find } });
  fireEvent.change(screen.getByLabelText("With"), { target: { value: replacement } });
  fireEvent.click(screen.getByRole("button", { name: "Review name changes" }));
}
async function confirm(count = 1) {
  const button = await screen.findByRole("button", { name: `Confirm ${count} renames` });
  fireEvent.click(screen.getByLabelText(/I reviewed the names and warnings/));
  fireEvent.click(button);
}

it("reviews literal uppercase replacement, freezes explicit selection and refreshes only on close", async () => {
  const selected = [{ ...first }]; const view = mount(selected);
  selected[0].materialName = "OUTSIDE-EDIT";
  view.client.getMaterial.mockResolvedValue(first);
  await review();
  expect(await screen.findByRole("button", { name: "Confirm 1 renames" })).toBeDisabled();
  expect(identityClient.plan).toHaveBeenCalledWith(first.id, { target_brand_id: first.publishedBrandId, main_category_code: "G03", target_parent: "CUSTOMER", material_name: "NEW-NEW" });
  expect(identityClient.confirm).not.toHaveBeenCalled();
  await confirm();
  await screen.findByText("Renamed");
  expect(identityClient.confirm).toHaveBeenCalledWith(first.id, expect.objectContaining({ material_name: "NEW-NEW", expected_proposal_hash: "a".repeat(64), expected_generation: 3, idempotency_key: expect.any(String), warnings_acknowledged: true }));
  expect(view.changed).not.toHaveBeenCalled(); expect(view.client.getMaterial).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(view.changed).toHaveBeenCalledOnce(); expect(view.close).toHaveBeenCalledOnce();
});

it("allows name-only changes for an unpublished Done material and shows the server-normalized name", async () => {
  mount([{ ...first, workflowStatus: "DONE" }]);
  await review("old", "new finish");
  await screen.findByText("NEW-FINISH-NEW-FINISH");
  await confirm(); await screen.findByText("Renamed");
  expect(identityClient.confirm).toHaveBeenCalledWith(first.id, expect.objectContaining({ material_name: "NEW FINISH-NEW FINISH", target_brand_id: first.publishedBrandId, main_category_code: first.mainCategoryCode, target_parent: "CUSTOMER" }));
});

it("does not plan or mutate unmatched, published, archived or unlinked materials", async () => {
  mount([{ ...first, isPublished: true }, { ...second, isArchived: true }, { ...third, folderPath: null }]);
  await review(); await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("3 blocked"));
  expect(identityClient.plan).not.toHaveBeenCalled(); expect(identityClient.confirm).not.toHaveBeenCalled();
});

it("rejects more than 100 explicit items without querying operation histories", () => {
  mount(Array.from({ length: 101 }, (_, i) => ({ ...first, id: String(i) })));
  expect(screen.getByRole("alert")).toHaveTextContent("between 1 and 100");
  expect(identityClient.operations).not.toHaveBeenCalled();
});

it("treats search text as literal and leaves unmatched names unchanged", async () => {
  const material = { ...first, materialName: "A.B-AXB" };
  vi.mocked(identityClient.plan).mockImplementation(async (_id, target) => proposal(material, target.material_name));
  mount([material, second]); await review("a.b", "new");
  await screen.findByRole("button", { name: "Confirm 1 renames" });
  expect(identityClient.plan).toHaveBeenCalledOnce();
  expect(identityClient.plan).toHaveBeenCalledWith(first.id, expect.objectContaining({ material_name: "NEW-AXB" }));
  expect(screen.getByText("Unchanged")).toBeInTheDocument();
});

it("clears a previously reviewed plan immediately after the replacement input changes", async () => {
  mount(); await review(); await screen.findByRole("button", { name: "Confirm 1 renames" });
  fireEvent.change(screen.getByLabelText("With"), { target: { value: "different" } });
  expect(screen.queryByRole("button", { name: /Confirm .* renames/ })).not.toBeInTheDocument();
  expect(identityClient.confirm).not.toHaveBeenCalled();
});

it("reports a canonical no-op as unchanged without confirming", async () => {
  const error = new ApiError(409, "Identity unchanged"); error.code = "IDENTITY_UNCHANGED";
  vi.mocked(identityClient.plan).mockRejectedValue(error);
  mount(); await review("-", " "); await screen.findByText("Unchanged");
  expect(identityClient.confirm).not.toHaveBeenCalled();
  expect(screen.getByRole("status")).toHaveTextContent("0 blocked");
});

it("blocks a stale selected record before any source plan", async () => {
  const view = mount(); view.client.getMaterial.mockResolvedValue({ ...first, updatedAt: "2026-10-02T12:00:00Z" });
  await review(); await screen.findByText(/Close this dialog and reload the selection/);
  expect(identityClient.plan).not.toHaveBeenCalled(); expect(identityClient.confirm).not.toHaveBeenCalled();
});

it("rejects a plan whose source raced the read or whose target changes the customer", async () => {
  vi.mocked(identityClient.plan).mockResolvedValue({ ...proposal(first), source: { ...context(first), name: "OTHER" }, target: { ...proposal(first).target, brandId: second.id } });
  mount(); await review(); await screen.findByText(/changes more than the material name/);
  expect(screen.queryByRole("button", { name: /Confirm .* renames/ })).not.toBeInTheDocument();
});

it("rechecks the material immediately before confirmation and never sends a stale plan", async () => {
  const view = mount(); await review(); await screen.findByRole("button", { name: "Confirm 1 renames" });
  view.client.getMaterial.mockResolvedValue({ ...first, updatedAt: "2026-10-02T12:00:00Z" });
  await confirm(); await screen.findByText(/changed after review/);
  expect(identityClient.confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Close" })); expect(view.changed).not.toHaveBeenCalled();
});

it("stops at an unknown result, retains the exact key through a 403, then resumes without repeating completed items", async () => {
  vi.mocked(identityClient.confirm).mockImplementation(async id => operation(fixtures.find(material => material.id === id)!));
  let secondCalls = 0;
  vi.mocked(identityClient.confirm).mockImplementation(async id => {
    if (id === second.id) {
      secondCalls++;
      if (secondCalls === 1) throw new TypeError("Lost response");
      if (secondCalls === 2) throw new ApiError(403, "Permission changed");
      return operation(second, "RUNNING");
    }
    return operation(fixtures.find(material => material.id === id)!);
  });
  mount(fixtures); await review(); await confirm(3);
  await waitFor(() => expect(screen.getByRole("button", { name: "Recover rename and continue" })).toBeEnabled());
  expect(identityClient.confirm).toHaveBeenCalledTimes(2); expect(requestNavigation("/orders")).toBe(false);
  expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Recover rename and continue" }));
  await waitFor(() => expect(identityClient.confirm).toHaveBeenCalledTimes(3));
  await waitFor(() => expect(screen.getByRole("button", { name: "Recover rename and continue" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Recover rename and continue" }));
  await waitFor(() => expect(identityClient.confirm).toHaveBeenCalledTimes(4));
  await waitFor(() => expect(screen.getByRole("button", { name: "Recover rename and continue" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Recover rename and continue" }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("3 renamed"));
  const attempts = vi.mocked(identityClient.confirm).mock.calls.filter(([id]) => id === second.id);
  expect(attempts[1]).toEqual(attempts[0]); expect(attempts[2]).toEqual(attempts[0]);
  expect(vi.mocked(identityClient.confirm).mock.calls.filter(([id]) => id === first.id)).toHaveLength(1);
  expect(identityClient.resume).toHaveBeenCalledWith(second.id, operation(second).id);
  expect(requestNavigation("/orders")).toBe(true);
});

it("treats an initial definitive conflict as blocked, with no repeated confirmation", async () => {
  vi.mocked(identityClient.confirm).mockRejectedValue(new ApiError(409, "Source plan changed"));
  mount(); await review(); await confirm(); await screen.findByText("Source plan changed");
  expect(screen.getByRole("button", { name: "Close" })).toBeEnabled();
  expect(requestNavigation("/orders")).toBe(true); expect(identityClient.confirm).toHaveBeenCalledOnce();
});

it("finds an older active identity operation on the next history page and recovers without a new plan", async () => {
  const old = Array.from({ length: 100 }, (_, index) => ({ ...operation(), id: `00000000-0000-4000-8000-${String(index + 100).padStart(12, "0")}` }));
  vi.mocked(identityClient.operations).mockResolvedValueOnce({ enabled: true, items: old }).mockResolvedValueOnce({ enabled: true, items: [operation(first, "RECOVERY_REQUIRED")] });
  const view = mount();
  fireEvent.click(await screen.findByRole("button", { name: "Recover rename and continue" }));
  await screen.findByText("Recovered");
  expect(screen.getByText(/Close and reopen this dialog/)).toBeInTheDocument();
  expect(identityClient.operations).toHaveBeenLastCalledWith(first.id, old.at(-1)!.id);
  expect(identityClient.plan).not.toHaveBeenCalled(); expect(identityClient.confirm).not.toHaveBeenCalled();
  expect(identityClient.resume).toHaveBeenCalledWith(first.id, operation().id);
  expect(view.changed).not.toHaveBeenCalled();
});

it("keeps an unexpected material receipt uncertain and stops the batch", async () => {
  vi.mocked(identityClient.confirm).mockResolvedValue(operation(third));
  mount([first, second]); await review(); await confirm(2);
  await screen.findByText("Outcome unknown");
  expect(identityClient.confirm).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
});

it("does not dispatch the next plan after the dialog unmounts", async () => {
  let resolve!: (plan: IdentityPlan) => void;
  vi.mocked(identityClient.plan).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const view = mount([first, second]); await review(); await waitFor(() => expect(identityClient.plan).toHaveBeenCalledOnce());
  view.unmount(); resolve(proposal(first)); await new Promise(done => setTimeout(done, 0));
  expect(identityClient.plan).toHaveBeenCalledOnce(); expect(identityClient.confirm).not.toHaveBeenCalled();
});
