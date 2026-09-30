import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CustomerRenamePanel } from "./CustomerRenamePanel";
import { directoryClient, type CustomerRenameOperation, type CustomerRenamePlan } from "../api/directoryClient";
import { customers } from "../test/directoryFixtures";
import { setSessionToken } from "../auth/sessionTransport";
import { requestNavigation } from "../navigationGuard";
import { ApiError } from "../api/errors";

const customer = customers[0];
const plan = { ready: true, proposalHash: "fixed-plan", materialCount: 2, issues: [] };
const operation: CustomerRenameOperation = { id: "00000000-0000-4000-8000-000000000090", status: "COMPLETED", completedCount: 2, totalCount: 2, customer, failures: [] };
beforeEach(() => {
  setSessionToken("test-session");
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  vi.spyOn(directoryClient, "customerRenameOperations").mockResolvedValue([]);
  vi.spyOn(directoryClient, "customerRenamePlan").mockResolvedValue(plan);
  vi.spyOn(directoryClient, "renameCustomer").mockResolvedValue(operation);
});
afterEach(() => { vi.restoreAllMocks(); setSessionToken(null); });
function mount() { const onChanged = vi.fn(); render(<CustomerRenamePanel customer={customer} disabled={false} onChanged={onChanged} onBusyChange={vi.fn()} />); return { onChanged }; }
async function open() { const button = screen.getByRole("button", { name: "Edit name" }); await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button); return screen.getByRole("dialog", { name: "Edit customer name" }); }
it("keeps historical renames opt-in and confirms the exact checked plan", async () => {
  const { onChanged } = mount(), dialog = await open();
  expect(within(dialog).getByRole("checkbox")).not.toBeChecked();
  expect(directoryClient.customerRenamePlan).not.toHaveBeenCalled();
  fireEvent.change(within(dialog).getByLabelText("New customer name"), { target: { value: "New name" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Confirm customer rename" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
  const payload = { name: "New name", rename_materials: false, expected_updated_at: customer.updatedAt };
  expect(directoryClient.customerRenamePlan).toHaveBeenCalledWith(customer.id, payload);
  expect(directoryClient.renameCustomer).toHaveBeenCalledWith(customer.id, payload, "fixed-plan", expect.any(String));
});
it("requires an explicit historical rename choice and explains unpublishing", async () => {
  mount(); const dialog = await open();
  fireEvent.click(within(dialog).getByRole("checkbox", { name: "Rename existing materials and mark them unpublished" }));
  expect(within(dialog).getByText(/renames existing material folders and files/)).toBeVisible();
  fireEvent.change(within(dialog).getByLabelText("Prefix for new materials"), { target: { value: "NEW-PREFIX" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Confirm customer rename" }));
  await waitFor(() => expect(directoryClient.renameCustomer).toHaveBeenCalledWith(customer.id, expect.objectContaining({ rename_materials: true, folder_prefix: "NEW-PREFIX" }), "fixed-plan", expect.any(String)));
});
it("stops on an unsafe plan without writing", async () => {
  vi.mocked(directoryClient.customerRenamePlan).mockResolvedValue({ ...plan, ready: false, issues: ["Target folder already exists."] });
  mount(); const dialog = await open(); fireEvent.click(within(dialog).getByRole("button", { name: "Confirm customer rename" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("Target folder already exists.");
  expect(directoryClient.renameCustomer).not.toHaveBeenCalled(); expect(requestNavigation("/materials")).toBe(true);
});
it("retains the plan and idempotency key across an uncertain rename and later 403", async () => {
  vi.mocked(directoryClient.renameCustomer).mockRejectedValueOnce(new TypeError("Lost reply")).mockRejectedValueOnce(new ApiError(403, "Access changed")).mockResolvedValue(operation);
  const { onChanged } = mount(), dialog = await open(); fireEvent.click(within(dialog).getByRole("button", { name: "Confirm customer rename" }));
  fireEvent.click(await within(dialog).findByRole("button", { name: "Retry same rename request" }));
  await waitFor(() => expect(directoryClient.renameCustomer).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(within(dialog).getByRole("button", { name: "Retry same rename request" })).toBeEnabled());
  expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled(); expect(requestNavigation("/materials")).toBe(false);
  fireEvent.click(within(dialog).getByRole("button", { name: "Retry same rename request" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(directoryClient.customerRenamePlan).toHaveBeenCalledOnce();
  const calls = vi.mocked(directoryClient.renameCustomer).mock.calls; expect(calls[1]).toEqual(calls[0]); expect(calls[2]).toEqual(calls[0]);
});
it("does not mutate after the session changes while preparing", async () => {
  let finish!: (value: CustomerRenamePlan) => void;
  vi.mocked(directoryClient.customerRenamePlan).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  mount(); const dialog = await open(); fireEvent.click(within(dialog).getByRole("button", { name: "Confirm customer rename" }));
  setSessionToken("another-session"); await act(async () => finish(plan));
  expect(directoryClient.renameCustomer).not.toHaveBeenCalled(); expect(within(dialog).getByRole("alert")).toHaveTextContent("session changed");
});
it("recovers an existing journal rather than starting another rename", async () => {
  vi.mocked(directoryClient.customerRenameOperations).mockResolvedValue([{ ...operation, status: "RECOVERY_REQUIRED", completedCount: 1, failures: ["material: FILE_IN_USE"] }]);
  const resume = vi.spyOn(directoryClient, "resumeCustomerRename").mockResolvedValue(operation), { onChanged } = mount();
  fireEvent.click(await screen.findByRole("button", { name: "Resume customer rename" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
  expect(resume).toHaveBeenCalledWith(customer.id, operation.id); expect(directoryClient.renameCustomer).not.toHaveBeenCalled();
});
it("treats earlier partial outcomes as history and permits a fresh explicit rename", async () => {
  vi.mocked(directoryClient.customerRenameOperations).mockResolvedValue([{ ...operation, status: "PARTIAL", completedCount: 1, failures: ["material: FILE_IN_USE"] }, operation]);
  mount(); await screen.findByText("Previous partial rename results (1)");
  expect(screen.queryByRole("button", { name: "Resume customer rename" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("Previous partial rename results (1)"));
  expect(screen.getByText(/later rename may already have corrected/)).toBeVisible();
  expect(screen.getByText(/To retry remaining materials/)).toBeVisible();
  expect(await open()).toBeVisible();
});
