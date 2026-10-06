import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CustomerAiBriefDialog } from "./CustomerAiBriefDialog";
import { customerAiBriefClient, type CustomerAiReceipt } from "../api/customerAiBriefClient";
import { aiCustomers, customerAiBatch, customerAiBrief, customerAiReceipt, customerAiResults, customerAiRow } from "../test/customerAiFixtures";
import { ApiError } from "../api/errors";
import { setSessionToken } from "../auth/sessionTransport";
import { requestNavigation } from "../navigationGuard";

const [first, second] = aiCustomers;
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  vi.spyOn(customerAiBriefClient, "generate").mockResolvedValue(customerAiBrief());
  vi.spyOn(customerAiBriefClient, "review").mockImplementation(async (results, ids = results.items.map(row => row.customer_id)) => ({ batch_id: customerAiBatch, items: ids.map(customerAiRow) }));
  vi.spyOn(customerAiBriefClient, "apply").mockImplementation(async id => customerAiReceipt(id));
  Object.defineProperty(URL, "createObjectURL", { configurable: true, writable: true, value: vi.fn(() => "blob:customer-ai") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, writable: true, value: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});
afterEach(() => { vi.restoreAllMocks(); setSessionToken(null); });
function mount(customers = [first]) { const changed = vi.fn(), close = vi.fn(); return { ...render(<CustomerAiBriefDialog customers={customers} onClose={close} onChanged={changed} />), changed, close }; }
async function importResults(ids = [first.id]) {
  const file = new File([JSON.stringify(customerAiResults(ids))], "customer-results.json", { type: "application/json" });
  Object.defineProperty(file, "text", { value: async () => JSON.stringify(customerAiResults(ids)) });
  fireEvent.change(screen.getByLabelText("Import Customer AI results"), { target: { files: [file] } });
  await screen.findByRole("region", { name: "Customer AI review" });
}
const select = (name = first.name) => fireEvent.click(screen.getByRole("checkbox", { name: `I reviewed the description, website and sources for ${name} and want to apply these changes.` }));

it("downloads a brief for only the frozen selection with matched upload/download buttons", async () => {
  const view = mount(); view.rerender(<CustomerAiBriefDialog customers={[second]} onClose={view.close} onChanged={view.changed} />);
  fireEvent.click(screen.getByRole("button", { name: "Download JSON brief" }));
  await screen.findByText(/JSON brief downloaded/);
  expect(customerAiBriefClient.generate).toHaveBeenCalledExactlyOnceWith([{ id: first.id, expected_updated_at: first.updatedAt }]);
  expect(screen.getByRole("button", { name: "Download JSON brief" }).className).toBe(screen.getByRole("button", { name: "Import AI results" }).className);
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce(); expect(customerAiBriefClient.apply).not.toHaveBeenCalled();
});

it("supports standalone imports after restart and recognizes already-applied rows", async () => {
  vi.mocked(customerAiBriefClient.review).mockResolvedValue({ batch_id: customerAiBatch, items: [{ ...customerAiRow(), status: "ALREADY_APPLIED", applicable: false }, customerAiRow(second.id)] });
  render(<CustomerAiBriefDialog onClose={vi.fn()} onChanged={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Download JSON brief" })).not.toBeInTheDocument();
  await importResults([first.id, second.id]);
  expect(customerAiBriefClient.review).toHaveBeenCalledWith(customerAiResults([first.id, second.id]), [first.id, second.id]);
  expect(screen.getByText(/Already applied from this JSON file/)).toBeVisible();
  expect(customerAiBriefClient.apply).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Accept all 1 changes" }));
  await screen.findByText("Saved");
  expect(customerAiBriefClient.apply).toHaveBeenCalledExactlyOnceWith(second.id, expect.any(Object));
});

it("requires separate description and website replacement consent for individual application", async () => {
  vi.mocked(customerAiBriefClient.review).mockResolvedValue({ batch_id: customerAiBatch, items: [{ ...customerAiRow(), current_description: "Old description", current_website: "https://old-manufacturer.com/", requires_description_overwrite: true, requires_website_overwrite: true, status: "OVERWRITE_REQUIRED" }] });
  const view = mount(); await importResults();
  const selected = screen.getByRole("checkbox", { name: /I reviewed the description/ }); expect(selected).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: `I approve replacing the existing description for ${first.name}.` })); expect(selected).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: `I approve replacing the existing website for ${first.name}.` }));
  select(); fireEvent.click(screen.getByRole("button", { name: "Apply 1 reviewed changes" }));
  await screen.findByText("Saved");
  expect(customerAiBriefClient.apply).toHaveBeenCalledWith(first.id, expect.objectContaining({ overwrite_description: true, overwrite_website: true }));
  expect(view.changed).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(view.changed).toHaveBeenCalledOnce(); expect(view.close).toHaveBeenCalledOnce();
});

it("accepts all eligible proposals with explicit replacement counts while skipping uncertainty", async () => {
  vi.mocked(customerAiBriefClient.review).mockResolvedValue({ batch_id: customerAiBatch, items: [
    { ...customerAiRow(), current_description: "Old", current_website: "https://old.com/", requires_description_overwrite: true, requires_website_overwrite: true, status: "OVERWRITE_REQUIRED" },
    { ...customerAiRow(second.id), status: "NEEDS_REVIEW", applicable: false, needs_review: true },
  ] });
  mount([first, second]); await importResults([first.id, second.id]);
  fireEvent.click(screen.getByRole("button", { name: "Accept all 1 changes (replace 1 existing description and 1 existing website)" }));
  await screen.findByText("Saved");
  expect(customerAiBriefClient.apply).toHaveBeenCalledExactlyOnceWith(first.id, expect.objectContaining({ overwrite_description: true, overwrite_website: true }));
  expect(screen.getByText(/Resolve its uncertainty/)).toBeVisible();
});

it("shows null proposals as keep-current and displays cited sources safely", async () => {
  const row = customerAiRow(); row.proposed_description = null; row.result!.description = null;
  vi.mocked(customerAiBriefClient.review).mockResolvedValue({ batch_id: customerAiBatch, items: [row] });
  mount(); await importResults();
  expect(screen.getByText("Keep current description")).toBeVisible();
  for (const link of within(screen.getByRole("region", { name: "Customer AI review" })).getAllByRole("link")) expect(link).toHaveAttribute("rel", "noopener noreferrer");
  select(); fireEvent.click(screen.getByRole("button", { name: "Apply 1 reviewed changes" })); await screen.findByText("Saved");
  expect(customerAiBriefClient.apply).toHaveBeenCalledWith(first.id, expect.objectContaining({ result: expect.objectContaining({ description: null }) }));
});

it("retains exact unknown requests through a later 403 and resumes remaining customers", async () => {
  vi.mocked(customerAiBriefClient.apply).mockRejectedValueOnce(new TypeError("Lost response")).mockRejectedValueOnce(new ApiError(403, "Forbidden"));
  mount([first, second]); await importResults([first.id, second.id]);
  fireEvent.click(screen.getByRole("button", { name: "Accept all 2 changes" }));
  await screen.findByText("Outcome unknown"); const firstPacket = vi.mocked(customerAiBriefClient.apply).mock.calls[0];
  expect(requestNavigation("/customers")).toBe(false);
  expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry same save and continue" }));
  await waitFor(() => expect(customerAiBriefClient.apply).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByRole("button", { name: "Retry same save and continue" })).toBeEnabled());
  expect(requestNavigation("/customers")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Retry same save and continue" }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("2 saved"));
  const calls = vi.mocked(customerAiBriefClient.apply).mock.calls;
  expect(calls).toHaveLength(4); expect(calls[1]).toEqual(firstPacket); expect(calls[2]).toEqual(firstPacket); expect(calls[3][0]).toBe(second.id);
  expect(requestNavigation("/customers")).toBe(true);
});

it("stops sending rows when the active session changes during an unresolved save", async () => {
  let resolve!: (value: CustomerAiReceipt) => void;
  vi.mocked(customerAiBriefClient.apply).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  mount([first, second]); await importResults([first.id, second.id]); fireEvent.click(screen.getByRole("button", { name: "Accept all 2 changes" }));
  await waitFor(() => expect(customerAiBriefClient.apply).toHaveBeenCalledOnce());
  await act(async () => { setSessionToken(null); resolve(customerAiReceipt()); });
  expect(customerAiBriefClient.apply).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "Retry same save and continue" })).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("session changed");
});

it("blocks invalid selections and rejects oversized files before reading", async () => {
  const empty = mount([]); expect(screen.getByRole("button", { name: "Download JSON brief" })).toBeDisabled(); empty.unmount();
  const duplicate = mount([first, first]); expect(screen.getByRole("button", { name: "Download JSON brief" })).toBeDisabled(); duplicate.unmount();
  const oversized = mount(Array.from({ length: 101 }, () => first)); expect(screen.getByRole("button", { name: "Download JSON brief" })).toBeDisabled(); oversized.unmount();
  mount(); const text = vi.fn(); fireEvent.change(screen.getByLabelText("Import Customer AI results"), { target: { files: [{ size: 5 * 1024 ** 2 + 1, text }] } });
  await screen.findByRole("alert"); expect(text).not.toHaveBeenCalled(); expect(customerAiBriefClient.review).not.toHaveBeenCalled();
});
