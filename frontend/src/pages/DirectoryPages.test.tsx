import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { CustomersPage } from "./CustomersPage";
import { OrdersPage } from "./OrdersPage";
import { DirectoryRecordPage } from "./DirectoryRecordPage";
import { httpApiClient } from "../api/client";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import { processorDto } from "../test/materialFixtures";
import { customerDtos, orderDtos } from "../test/directoryFixtures";
import { requestNavigation } from "../navigationGuard";
import { directoryClient, parseCustomer, parseOrder } from "../api/directoryClient";
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const customer = customerDtos[0], order = orderDtos[0];
function manager(children: ReactNode) { return <SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>{children}</SessionContext.Provider>; }
function backend(options: { folder?: boolean; order?: object; write?: (path: string, init: RequestInit) => Promise<Response> } = {}) {
  let customerRow = { ...customer }, orderRow = { ...order, ...options.order };
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method && init.method !== "GET") {
      if (options.write) return options.write(path, init);
      const payload = JSON.parse(String(init.body));
      if (path.endsWith("/folder")) return json({ status: "PENDING" }, 202);
      if (path.startsWith("/api/customers")) { customerRow = { ...customerRow, ...payload, updated_at: "2026-10-01T00:00:00Z" }; return json(customerRow); }
      orderRow = { ...orderRow, ...payload, updated_at: "2026-10-01T00:00:00Z" }; return json(orderRow);
    }
    if (path === "/api/customers") return json([customerRow, customerDtos[1]]);
    if (path === `/api/customers/${customer.id}`) return json(customerRow);
    if (path.endsWith("/rename-operations")) return json([]);
    if (path === "/api/orders") return json([orderRow]);
    if (path === "/api/orders/defaults") return json({ number: "0254", starting_date: "2026-10-01" });
    if (path === `/api/orders/${order.id}`) return json(orderRow);
    if (path.endsWith("/folder")) return json({ enabled: options.folder === true });
    if (path === "/api/internal-users") return json([processorDto]);
    return json({ detail: "Not found" }, 404);
  }); vi.stubGlobal("fetch", fetch); return fetch;
}
function editor(kind: "customer" | "order", id?: string) { const saved = vi.fn(); const view = render(manager(<DirectoryRecordPage kind={kind} id={id} client={httpApiClient} navigate={vi.fn()} onSaved={saved} />)); return { saved, ...view }; }
beforeEach(() => { localStorage.clear(); setSessionToken("t".repeat(43)); HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); }; HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); }; });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); setSessionToken(null); localStorage.clear(); });
it("filters Customers using material main categories and sends versioned inline edits", async () => {
  const fetch = backend(); render(manager(<CustomersPage navigate={vi.fn()} />));
  await screen.findByRole("link", { name: customer.name });
  expect(screen.queryByRole("textbox", { name: `Customer for ${customer.name}` })).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "Main category" }), { target: { value: "H01" } });
  expect(screen.queryByRole("link", { name: customerDtos[1].name })).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: `Status for ${customer.name}` }), { target: { value: "Test sample" } });
  await screen.findByText("Saved. Refresh to reapply filters.");
  const call = fetch.mock.calls.find(([, init]) => init?.method === "PATCH")!;
  expect(call[0]).toBe(`/api/customers/${customer.id}`);
  expect(JSON.parse(String(call[1]?.body))).toEqual({ status: "Test sample", expected_updated_at: customer.updated_at });
  expect(call[1]?.headers).toMatchObject({ "Idempotency-Key": expect.any(String), "X-CSRF-Token": "t".repeat(43) });
});
it("filters Customer creation and update dates independently and clears the bounds", async () => {
  vi.spyOn(directoryClient, "customers").mockResolvedValue([
    parseCustomer({ ...customer, created_at: "2026-01-01T23:59:00Z", updated_at: "2026-03-02T12:00:00Z" }),
    parseCustomer({ ...customerDtos[1], created_at: "2026-02-01T00:01:00Z", updated_at: "2026-04-02T12:00:00Z" }),
  ]);
  render(manager(<CustomersPage navigate={vi.fn()} />)); await screen.findByRole("link", { name: customer.name });
  fireEvent.change(screen.getByLabelText("Created from"), { target: { value: "2026-02-01" } });
  expect(screen.queryByRole("link", { name: customer.name })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Created to"), { target: { value: "2026-02-01" } });
  expect(screen.getByRole("link", { name: customerDtos[1].name })).toBeVisible();
  fireEvent.change(screen.getByLabelText("Updated to"), { target: { value: "2026-03-31" } });
  expect(screen.getByText("No customers match these filters.")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  fireEvent.change(screen.getByLabelText("Updated from"), { target: { value: "2026-03-02" } });
  fireEvent.change(screen.getByLabelText("Updated to"), { target: { value: "2026-03-02" } });
  expect(screen.getByRole("link", { name: customer.name })).toBeVisible();
  expect(screen.queryByRole("link", { name: customerDtos[1].name })).not.toBeInTheDocument();
});
it("automatically resizes the Customers workspace without losing filters, selections or unfinished cell edits", async () => {
  let resized: (() => void) | undefined;
  const media = { matches: true, addEventListener: vi.fn((_name: string, callback: () => void) => { resized = callback; }), removeEventListener: vi.fn() };
  vi.stubGlobal("matchMedia", vi.fn(() => media));
  localStorage.setItem("customers.workspace-view", "page");
  const fetch = backend(); render(manager(<CustomersPage navigate={vi.fn()} />));
  await screen.findByRole("link", { name: customer.name });
  expect(screen.queryByRole("checkbox", { name: "Fixed workspace" })).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Database results" }).parentElement).toHaveClass("database-table-viewport--contained");
  fireEvent.change(screen.getByRole("searchbox", { name: "Search customers" }), { target: { value: customer.name } });
  fireEvent.click(screen.getByRole("checkbox", { name: `Select ${customer.name}` }));
  const editor = screen.getByRole("textbox", { name: `Notes for ${customer.name}` });
  fireEvent.change(editor, { target: { value: "Unfinished customer note" } });
  const count = fetch.mock.calls.length;
  act(() => { media.matches = false; resized?.(); });
  expect(screen.getByRole("region", { name: "Database results" }).parentElement).not.toHaveClass("database-table-viewport--contained");
  expect(screen.getByRole("checkbox", { name: `Select ${customer.name}` })).toBeChecked();
  expect(screen.getByRole("textbox", { name: `Notes for ${customer.name}` })).toBe(editor);
  expect(editor).toHaveValue("Unfinished customer note");
  expect(screen.getByRole("searchbox", { name: "Search customers" })).toHaveValue(customer.name);
  expect(fetch.mock.calls).toHaveLength(count);
  act(() => { media.matches = true; resized?.(); });
  expect(screen.getByRole("checkbox", { name: `Select ${customer.name}` })).toBeChecked();
  expect(screen.getByRole("textbox", { name: `Notes for ${customer.name}` })).toBe(editor);
  expect(editor).toHaveValue("Unfinished customer note");
  expect(screen.getByRole("region", { name: "Database results" }).parentElement).toHaveClass("database-table-viewport--contained");
  expect(fetch.mock.calls).toHaveLength(count);
});
it("falls back in small windows and restores the automatic workspace after resizing", async () => {
  let resized: (() => void) | undefined;
  const media = { matches: false, addEventListener: vi.fn((_name: string, callback: () => void) => { resized = callback; }), removeEventListener: vi.fn() };
  vi.stubGlobal("matchMedia", vi.fn(() => media));
  backend(); const { unmount } = render(manager(<CustomersPage navigate={vi.fn()} />));
  await screen.findByRole("link", { name: customer.name });
  expect(screen.queryByRole("checkbox", { name: "Fixed workspace" })).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Database results" }).parentElement).not.toHaveClass("database-table-viewport--contained");
  expect(screen.getByLabelText("Created from")).toBeVisible();
  act(() => { media.matches = true; resized?.(); });
  expect(screen.getByRole("region", { name: "Database results" }).parentElement).toHaveClass("database-table-viewport--contained");
  unmount(); expect(media.removeEventListener).toHaveBeenCalledWith("change", resized);
});
it("preserves Orders filters, selection and the same unfinished editor across both workspace sizes", async () => {
  let resized: (() => void) | undefined;
  const media = { matches: true, addEventListener: vi.fn((_name: string, callback: () => void) => { resized = callback; }), removeEventListener: vi.fn() };
  vi.stubGlobal("matchMedia", vi.fn(() => media));
  const fetch = backend(); const { unmount } = render(manager(<OrdersPage client={httpApiClient} navigate={vi.fn()} />));
  await screen.findByRole("link", { name: order.number });
  expect(screen.getByRole("region", { name: "Database results" }).parentElement).toHaveClass("database-table-viewport--contained");
  fireEvent.change(screen.getByRole("searchbox", { name: "Search orders" }), { target: { value: order.number } });
  const rowLabel = `${order.number} · ${order.generated_name}`;
  fireEvent.click(screen.getByRole("checkbox", { name: `Select ${rowLabel}` }));
  const editor = screen.getByRole("textbox", { name: `Note for ${rowLabel}` });
  fireEvent.change(editor, { target: { value: "Unfinished order note" } });
  const count = fetch.mock.calls.length;
  act(() => { media.matches = false; resized?.(); });
  expect(screen.getByRole("region", { name: "Database results" }).parentElement).not.toHaveClass("database-table-viewport--contained");
  expect(screen.getByRole("checkbox", { name: `Select ${rowLabel}` })).toBeChecked();
  expect(screen.getByRole("textbox", { name: `Note for ${rowLabel}` })).toBe(editor);
  expect(editor).toHaveValue("Unfinished order note");
  expect(screen.getByRole("searchbox", { name: "Search orders" })).toHaveValue(order.number);
  expect(fetch.mock.calls).toHaveLength(count);
  act(() => { media.matches = true; resized?.(); });
  expect(screen.getByRole("region", { name: "Database results" }).parentElement).toHaveClass("database-table-viewport--contained");
  expect(screen.getByRole("checkbox", { name: `Select ${rowLabel}` })).toBeChecked();
  expect(screen.getByRole("textbox", { name: `Note for ${rowLabel}` })).toBe(editor);
  expect(editor).toHaveValue("Unfinished order note");
  expect(fetch.mock.calls).toHaveLength(count);
  unmount(); expect(media.removeEventListener).toHaveBeenCalledWith("change", resized);
});
it("shows all Notion order statuses and keeps folder renames separate from inline changes", async () => {
  const fetch = backend(); render(manager(<OrdersPage client={httpApiClient} navigate={vi.fn()} />));
  await screen.findByRole("link", { name: order.number });
  expect(within(screen.getByRole("combobox", { name: "Status" })).getAllByRole("option")).toHaveLength(14);
  const customerCell = screen.getByRole("combobox", { name: `Customer for ${order.number} · ${order.generated_name}` });
  expect(within(customerCell).getByRole("option", { name: "Not assigned" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all filtered rows" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Bulk property" }), { target: { value: "customer_id" } });
  expect(screen.getByRole("combobox", { name: "Bulk value" })).toHaveValue(customer.id);
  fireEvent.change(screen.getByRole("combobox", { name: `Status for ${order.number} · ${order.generated_name}` }), { target: { value: "Visualize" } });
  await screen.findByText("Saved. Refresh to reapply filters.");
  expect(fetch.mock.calls.filter(([path, init]) => path.endsWith("/folder") && init?.method === "POST")).toHaveLength(0);
});
it("filters Orders by the actual due date independently from the starting-date lower bound", async () => {
  backend();
  vi.spyOn(directoryClient, "orders").mockResolvedValue([
    parseOrder({ ...order, starting_date: "2026-08-01", due_date: "2026-10-01" }),
    parseOrder({ ...order, id: "10000000-0000-4000-8000-000000000002", number: "9992", starting_date: "2026-08-15", due_date: "2026-09-15" }),
    parseOrder({ ...order, id: "10000000-0000-4000-8000-000000000003", number: "9993", starting_date: "2026-08-15", due_date: null }),
  ]);
  render(manager(<OrdersPage client={httpApiClient} navigate={vi.fn()} />));
  await screen.findByRole("link", { name: order.number });
  expect(screen.queryByLabelText("Starting to")).not.toBeInTheDocument();
  fireEvent.change(within(screen.getByRole("group", { name: "Filter orders" })).getByLabelText("Due date"), { target: { value: "2026-09-30" } });
  expect(screen.queryByRole("link", { name: order.number })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "9992" })).toBeVisible();
  expect(screen.queryByRole("link", { name: "9993" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Starting from"), { target: { value: "2026-08-16" } });
  expect(screen.getByText("No orders match these filters.")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(screen.getByRole("link", { name: order.number })).toBeVisible();
  expect(screen.getByRole("link", { name: "9992" })).toBeVisible();
  expect(screen.getByRole("link", { name: "9993" })).toBeVisible();
});
it("creates a customer with only editable fields and without a separate Brand record", async () => {
  const fetch = backend(), { saved } = editor("customer");
  fireEvent.change(await screen.findByLabelText("Name *"), { target: { value: "  New customer  " } });
  fireEvent.change(screen.getByLabelText("Notes"), { target: { value: "#demo" } });
  fireEvent.submit(screen.getByRole("form", { name: "Add customer" }));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  const writes = fetch.mock.calls.filter(([, init]) => init?.method === "POST"); expect(writes).toHaveLength(1);
  expect(writes[0][0]).toBe("/api/customers");
  expect(JSON.parse(String(writes[0][1]?.body))).toMatchObject({ name: "New customer", notes: "#demo", brand_identifier: null, status: "Active cooperation" });
  expect(JSON.parse(String(writes[0][1]?.body))).not.toHaveProperty("company_id");
});
it("validates required customer fields and safe website URLs before sending", async () => {
  const fetch = backend(); editor("customer"); await screen.findByLabelText("Name *");
  fireEvent.change(screen.getByLabelText("Website"), { target: { value: "javascript:alert(1)" } });
  fireEvent.submit(screen.getByRole("form", { name: "Add customer" }));
  expect(await screen.findByRole("alert")).toHaveFocus(); expect(screen.getByLabelText("Name *")).toHaveAttribute("aria-invalid", "true");
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});
it("freezes an uncertain create request and reuses its idempotency key on retry", async () => {
  let writes = 0;
  const fetch = backend({ write: async () => { if (++writes === 1) throw new TypeError("Lost reply"); return json(customer); } });
  const { saved } = editor("customer"); fireEvent.change(await screen.findByLabelText("Name *"), { target: { value: "New customer" } });
  fireEvent.submit(screen.getByRole("form", { name: "Add customer" }));
  await screen.findByRole("button", { name: "Retry same request" }); expect(requestNavigation("/materials")).toBe(false); expect(screen.getByLabelText("Name *")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry same request" })); await waitFor(() => expect(saved).toHaveBeenCalled());
  const calls = fetch.mock.calls.filter(([, init]) => init?.method === "POST"); expect(calls).toHaveLength(2); expect(calls[0]).toEqual(calls[1]); expect(requestNavigation("/materials")).toBe(true);
});
it("prevents duplicate submits and ignores a result after unmount", async () => {
  let finish!: (response: Response) => void;
  const fetch = backend({ write: () => new Promise(resolve => { finish = resolve; }) });
  const { saved, unmount } = editor("customer"); fireEvent.change(await screen.findByLabelText("Name *"), { target: { value: "Pending" } });
  const form = screen.getByRole("form", { name: "Add customer" }); fireEvent.submit(form); fireEvent.submit(form);
  await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1));
  unmount(); await act(async () => finish(json(customer))); expect(saved).not.toHaveBeenCalled();
});
it("generates the uppercase Notion name and creates an order without assigning a number client-side", async () => {
  const fetch = backend(), { saved } = editor("order");
  const customerInput = await screen.findByLabelText("Customer *");
  expect(screen.getByLabelText("Number (automatic if empty)")).toHaveValue("0254");
  expect(screen.getByLabelText("Starting date *")).toHaveValue("2026-10-01");
  expect(within(customerInput).getByRole("option", { name: "Choose a customer" })).toBeDisabled();
  fireEvent.change(customerInput, { target: { value: customer.id } });
  fireEvent.change(screen.getByLabelText("Project type *"), { target: { value: "scanning_fabrics" } });
  fireEvent.change(screen.getByLabelText("Starting date *"), { target: { value: "2026-03-14" } });
  expect(screen.getByText(/0254_SWISSPEARL_SCANNING_FABRICS_032026/)).toBeVisible();
  fireEvent.submit(screen.getByRole("form", { name: "Add order" })); await waitFor(() => expect(saved).toHaveBeenCalled());
  const payload = JSON.parse(String(fetch.mock.calls.find(([, init]) => init?.method === "POST")![1]?.body));
  expect(payload).toMatchObject({ customer_id: customer.id, project_type: "scanning_fabrics", starting_date: "2026-03-14" }); expect(payload).not.toHaveProperty("number");
});
it("preserves a historical unlinked order while preventing Customer from being cleared", async () => {
  const fetch = backend({ order: { customer_id: null } }); editor("order", order.id);
  const customerInput = await screen.findByLabelText("Customer");
  expect(customerInput).toHaveValue("");
  expect(within(customerInput).getByRole("option", { name: "Choose a customer" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "Historical note" } });
  fireEvent.submit(screen.getByRole("form", { name: "Edit order" }));
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(true));
  expect(JSON.parse(String(fetch.mock.calls.find(([, init]) => init?.method === "PATCH")![1]?.body))).toEqual({ notes: "Historical note", expected_updated_at: order.updated_at });
});
it("sends an explicitly edited order number instead of the server suggestion", async () => {
  const fetch = backend(), { saved } = editor("order");
  fireEvent.change(await screen.findByLabelText("Customer *"), { target: { value: customer.id } });
  fireEvent.change(screen.getByLabelText("Project type *"), { target: { value: "SCANNING" } });
  fireEvent.change(screen.getByLabelText("Number (automatic if empty)"), { target: { value: "0280" } });
  fireEvent.submit(screen.getByRole("form", { name: "Add order" }));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  const call = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(String(call[1]?.body))).toMatchObject({ number: "0280", starting_date: "2026-10-01" });
});
it("sorts orders by created date, name and number without changing records", async () => {
  backend();
  vi.spyOn(directoryClient, "orders").mockResolvedValue([
    parseOrder({ ...order, number: "0020", generated_name: "ZETA", created_at: "2026-09-01T00:00:00Z" }),
    parseOrder({ ...order, id: "10000000-0000-4000-8000-000000000002", number: "0010", generated_name: "ALPHA", created_at: "2026-10-01T00:00:00Z" }),
  ]);
  render(manager(<OrdersPage client={httpApiClient} navigate={vi.fn()} />));
  await screen.findByRole("link", { name: "0010" });
  expect(screen.getByRole("columnheader", { name: "NUMBER" })).toBeVisible();
  const rows = () => within(screen.getByRole("table")).getAllByRole("link").map(link => link.textContent);
  expect(rows()).toEqual(["0010", "0020"]);
  fireEvent.change(screen.getByRole("combobox", { name: "Sort orders" }), { target: { value: "created-asc" } });
  expect(rows()).toEqual(["0020", "0010"]);
  fireEvent.change(screen.getByRole("combobox", { name: "Sort orders" }), { target: { value: "name-asc" } });
  expect(rows()).toEqual(["0010", "0020"]);
  fireEvent.change(screen.getByRole("combobox", { name: "Sort orders" }), { target: { value: "number-desc" } });
  expect(rows()).toEqual(["0020", "0010"]);
});
it("requires explicit folder confirmation and sends the exact record version", async () => {
  const fetch = backend({ folder: true, order: { folder_path: "R:/0. PROJECTS/OLD", folder_name_matches: false } }); editor("order", order.id);
  fireEvent.click(await screen.findByRole("button", { name: "Rename order folder…" }));
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  const dialog = screen.getByRole("dialog", { name: "Rename order folder" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Confirm rename" }));
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
  const call = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(call[0]).toBe(`/api/orders/${order.id}/folder`); expect(JSON.parse(String(call[1]?.body))).toEqual({ action: "RENAME", expected_updated_at: order.updated_at, confirmed: true });
});
it("shows disabled Notion sync honestly and preserves all linked people unless Responsible changes", async () => {
  const fetch = backend({ order: { responsible_notion_page_ids: [customer.id, order.id] } }); editor("order", order.id);
  expect(await screen.findByText("Notion sync is disabled")).toBeVisible(); expect(screen.queryByRole("button", { name: "Retry Notion synchronization" })).not.toBeInTheDocument();
  expect(screen.getByText(/also has 1 linked people/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "A note" } });
  fireEvent.submit(screen.getByRole("form", { name: "Edit order" }));
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(true));
  expect(JSON.parse(String(fetch.mock.calls.find(([, init]) => init?.method === "PATCH")![1]?.body))).toEqual({ notes: "A note", expected_updated_at: order.updated_at });
});

it("uploads a logo with the exact customer revision and rejects oversized images locally", async () => {
  const fetch = backend(); editor("customer", customer.id);
  const upload = await screen.findByLabelText("Upload logo");
  fireEvent.change(upload, { target: { files: [new File(["png"], "logo.png", { type: "image/png" })] } });
  fireEvent.click(screen.getByRole("button", { name: "Save logo" }));
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(true));
  const call = fetch.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(call[0]).toBe(`/api/customers/${customer.id}/logo`);
  expect(JSON.parse(String(call[1]?.body))).toEqual({ content_base64: "cG5n", filename: "logo.png", expected_updated_at: customer.updated_at });
  await screen.findByLabelText("Upload logo");
  fireEvent.change(screen.getByLabelText("Upload logo"), { target: { files: [new File([new Uint8Array(2 * 1024 * 1024 + 1)], "large.png", { type: "image/png" })] } });
  expect(await screen.findByRole("alert")).toHaveTextContent("up to 2 MB"); expect(screen.getByRole("button", { name: "Save logo" })).toBeDisabled();
});
