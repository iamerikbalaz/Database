import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CustomersPage } from "./CustomersPage";
import { DirectoryRecordPage } from "./DirectoryRecordPage";
import { CustomerAiBriefDialog } from "../components/CustomerAiBriefDialog";
import { directoryClient } from "../api/directoryClient";
import { catalogClient } from "../api/catalogClient";
import { mockApiClient } from "../api/client";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";
import { processorDto } from "../test/materialFixtures";
import { aiCustomers } from "../test/customerAiFixtures";
import type { ReactNode } from "react";

vi.mock("../components/CustomerAiBriefDialog", () => ({ CustomerAiBriefDialog: vi.fn(({ customers, onClose, onChanged }) =>
  <div role="dialog" aria-label="Customer AI integration"><p>{customers?.length} frozen customers</p><button onClick={() => { onChanged(); onClose(); }}>Finish Customer AI</button></div>) }));
const [first, second] = aiCustomers;
function session(child: ReactNode, role: Role = "ADMIN") { return <SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>{child}</SessionContext.Provider>; }
beforeEach(() => {
  localStorage.clear(); vi.spyOn(directoryClient, "customers").mockResolvedValue(aiCustomers);
  vi.spyOn(directoryClient, "customer").mockResolvedValue(first);
  vi.spyOn(directoryClient, "orders").mockResolvedValue([]);
  vi.spyOn(directoryClient, "history").mockResolvedValue([]);
  vi.spyOn(directoryClient, "customerRenameOperations").mockResolvedValue([]);
  vi.spyOn(catalogClient, "categories").mockResolvedValue([]);
});
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

it("opens Customer AI for only checked customers and refreshes current values after changes", async () => {
  render(session(<CustomersPage navigate={vi.fn()} />));
  await screen.findByRole("link", { name: first.name });
  expect(screen.getByRole("button", { name: "Generate AI brief (0)" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: `Select ${second.name}` }));
  fireEvent.click(screen.getByRole("button", { name: "Generate AI brief (1)" }));
  expect(vi.mocked(CustomerAiBriefDialog).mock.calls.at(-1)?.[0].customers).toEqual([second]);
  expect(screen.getByLabelText("Search customers")).toBeDisabled();
  vi.mocked(directoryClient.customers).mockResolvedValue(aiCustomers.map(row => row.id === second.id ? { ...row, description: "Saved AI description" } : row));
  fireEvent.click(screen.getByRole("button", { name: "Finish Customer AI" }));
  await waitFor(() => expect(directoryClient.customers).toHaveBeenCalledTimes(2));
  expect(await screen.findByLabelText(`Company description for ${second.name}`)).toHaveValue("Saved AI description");
});

it("blocks more than 100 selected customers and read-only roles", async () => {
  // This checks the bulk selection cap, independent of the editable property
  // grid; keep only required/name cells so parallel test runs stay inexpensive.
  localStorage.setItem("customers.columns.v1", JSON.stringify(["brand_identifier", "status", "categories", "website", "country", "is_published", "address", "shipping_address", "legal_name", "vat_id", "description", "notes", "is_active", "sync", "created", "updated"]));
  vi.mocked(directoryClient.customers).mockResolvedValue(Array.from({ length: 101 }, (_, index) => ({ ...first, id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`, name: `Customer ${index}` })));
  const view = render(session(<CustomersPage navigate={vi.fn()} />)); await screen.findByRole("link", { name: "Customer 0" });
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all filtered rows" }));
  expect(screen.getByRole("button", { name: "Generate AI brief (101)" })).toBeDisabled();
  view.unmount(); vi.mocked(directoryClient.customers).mockResolvedValue(aiCustomers);
  render(session(<CustomersPage navigate={vi.fn()} />, "PROCESSOR")); await screen.findByRole("link", { name: first.name });
  expect(screen.queryByRole("button", { name: /Generate AI brief/ })).not.toBeInTheDocument();
});

it("offers single-customer AI and protects unsaved profile edits", async () => {
  render(session(<DirectoryRecordPage kind="customer" id={first.id} client={mockApiClient} navigate={vi.fn()} onSaved={vi.fn()} />, "PRODUCTION_LEAD"));
  const button = await screen.findByRole("button", { name: "Generate Customer AI brief" });
  fireEvent.change(screen.getByLabelText("Company description"), { target: { value: "Unsaved manual text" } });
  expect(button).toBeDisabled(); expect(screen.getByText("Save your current edits before preparing an AI brief.")).toBeVisible();
  fireEvent.change(screen.getByLabelText("Company description"), { target: { value: "" } });
  fireEvent.click(button); expect(vi.mocked(CustomerAiBriefDialog).mock.calls.at(-1)?.[0].customers).toEqual([first]);
  expect(screen.getByLabelText("Website")).toBeDisabled();
});
