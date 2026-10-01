import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CatalogPage } from "./CatalogPage";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import type { Role } from "../auth/client";
import { httpApiClient } from "../api/client";
import { materialBrand, processorDto } from "../test/materialFixtures";
import { requestNavigation } from "../navigationGuard";

const category = { id: "10000000-0000-4000-8000-000000000001", value: "Stone", version: 1, is_active: true, abbreviation: "H", created_at: "2026-09-25T12:00:00Z" };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
beforeEach(() => {
  localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); setSessionToken(null); });
function setup(role: Role = "ADMIN", categories = [category]) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.endsWith("/brands")) return json([materialBrand]);
    if (init?.method === "POST") return json(category);
    if (init?.method === "PATCH") {
      const payload = JSON.parse(String(init.body));
      const row = categories.find(item => path.includes(item.id)) ?? category;
      return json({ ...row, version: row.version + 1, ...(payload.is_active !== undefined ? { is_active: payload.is_active } : { abbreviation: payload.abbreviation }), ...(payload.value ? { value: payload.value } : {}) });
    }
    if (path.endsWith("/collections")) return json([]);
    return json(categories);
  });
  vi.stubGlobal("fetch", fetch); setSessionToken("t".repeat(43));
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <CatalogPage client={httpApiClient} />
  </SessionContext.Provider>);
  return fetch;
}
async function openCreate() {
  await screen.findByText("Stone");
  fireEvent.click(screen.getByRole("button", { name: "Add catalog value" }));
  return screen.getByRole("dialog", { name: "Add catalog value" });
}
it("defaults catalog to A–Z and keeps sorting outside its filters without reloading", async () => {
  const fetch = setup("ADMIN", [category, { ...category, id: "10000000-0000-4000-8000-000000000002", value: "Alpine stone", abbreviation: "A", created_at: "2026-08-01T00:00:00Z" }]);
  await screen.findByText("Alpine stone");
  const sort = screen.getByRole("combobox", { name: "Sort catalog" });
  const names = () => within(screen.getByRole("table")).getAllByRole("button", { name: /^Edit category / }).map(button => button.getAttribute("aria-label"));
  expect(sort).toHaveValue("name-asc"); expect(sort.closest("fieldset")).toBeNull();
  expect(names()).toEqual(["Edit category Alpine stone", "Edit category Stone"]);
  const requests = fetch.mock.calls.length;
  fireEvent.change(sort, { target: { value: "created-desc" } });
  expect(names()).toEqual(["Edit category Stone", "Edit category Alpine stone"]);
  fireEvent.change(sort, { target: { value: "name-desc" } });
  expect(names()).toEqual(["Edit category Stone", "Edit category Alpine stone"]);
  expect(fetch).toHaveBeenCalledTimes(requests);
});
it("creates a brand collection with an explicit selected brand", async () => {
  const fetch = setup(); await openCreate();
  fireEvent.change(screen.getByRole("combobox", { name: "Value type" }), { target: { value: "collections" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Collection customer" }), { target: { value: materialBrand.id } });
  fireEvent.change(screen.getByLabelText("Catalog value"), { target: { value: "Studio" } });
  fireEvent.click(screen.getByRole("button", { name: "Create catalog value" })); await screen.findByText("Catalog change saved.");
  const call = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(call[0]).toBe("/api/collections"); expect(JSON.parse(String(call[1]?.body))).toEqual({ idempotency_key: expect.any(String), value: "Studio", brand_id: materialBrand.id });
});
it("changes availability inline without a reason and with the exact catalog version", async () => {
  const fetch = setup(); await screen.findByRole("checkbox", { name: "Active for Stone" });
  expect(screen.queryByLabelText("Reason for catalog change")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "Active for Stone" }));
  await screen.findByText("Saved. Refresh to reapply filters.");
  const call = fetch.mock.calls.find(([, init]) => init?.method === "PATCH")!;
  expect(JSON.parse(String(call[1]?.body))).toEqual({ idempotency_key: expect.any(String), expected_version: 1, is_active: false });
  expect(call[0]).toBe(`/api/online-categories/${category.id}/table`);
});
it("replays an unknown catalog mutation with its original key and frozen inputs", async () => {
  const fetch = setup(); const dialog = await openCreate();
  fireEvent.change(screen.getByLabelText("Catalog value"), { target: { value: "Wood" } }); fetch.mockRejectedValueOnce(new TypeError("Synthetic timeout"));
  fireEvent.click(screen.getByRole("button", { name: "Create catalog value" })); await screen.findByRole("alert");
  expect(requestNavigation("/materials")).toBe(false);
  expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled();
  fireEvent(dialog, new Event("cancel", { cancelable: true })); expect(dialog).toHaveAttribute("open");
  expect(screen.getByLabelText("Catalog value")).toBeDisabled(); fireEvent.click(screen.getByRole("button", { name: "Retry same catalog request" }));
  await waitFor(() => expect(screen.getByText("Catalog change saved.")).toBeVisible());
  expect(requestNavigation("/materials")).toBe(true);
  const calls = fetch.mock.calls.filter(([, init]) => init?.method === "POST"); expect(calls).toHaveLength(2); expect(calls[0][1]?.body).toBe(calls[1][1]?.body);
  expect(dialog).not.toHaveAttribute("open");
});
it.each(["PROCESSOR", "LEADERSHIP"] as const)("keeps %s catalog access read-only", async (role) => {
  setup(role); await screen.findByText("Stone"); expect(screen.queryByRole("button", { name: "Create catalog value" })).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox", { name: "Active for Stone" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Edit category Stone" })).not.toBeInTheDocument();
});

it("filters by persisted abbreviation, active state and creation date inside fixed type tabs", async () => {
  setup("ADMIN", [category, { ...category, id: "10000000-0000-4000-8000-000000000002", value: "Metal / Tiles", abbreviation: "K03", is_active: false, created_at: "2026-08-01T00:00:00Z" }]);
  await screen.findByText("Metal / Tiles");
  fireEvent.change(screen.getByRole("searchbox", { name: "Search catalog" }), { target: { value: "k03" } });
  expect(screen.queryByText("Stone")).not.toBeInTheDocument();
  expect(screen.getByText("Metal / Tiles")).toBeVisible();
  fireEvent.change(screen.getByRole("combobox", { name: "Active filter" }), { target: { value: "active" } });
  expect(screen.getByText("No catalog values match these filters.")).toBeVisible();
  fireEvent.change(screen.getByRole("combobox", { name: "Active filter" }), { target: { value: "all" } });
  fireEvent.change(screen.getByLabelText("Created from"), { target: { value: "2026-09-01" } });
  expect(screen.getByText("No catalog values match these filters.")).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Brand collections" }));
  expect(screen.getByRole("tab", { name: "Brand collections" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("combobox", { name: "Customer filter" })).toBeVisible();
});

it("edits name and abbreviation only in the explicit dialog with a folder warning", async () => {
  const fetch = setup(); await screen.findByRole("button", { name: "Edit category Stone" });
  expect(screen.queryByRole("textbox", { name: "Abbreviation for Stone" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit category Stone" }));
  expect(screen.getByRole("dialog", { name: "Edit category" })).toBeVisible();
  expect(screen.getByText(/This change will not rename existing material folders/)).toBeVisible();
  expect(screen.getByLabelText("Catalog value")).toHaveValue("Stone");
  expect(screen.getByLabelText("Catalog value")).toHaveFocus();
  fireEvent.change(screen.getByLabelText("Catalog value"), { target: { value: "Natural stone" } });
  fireEvent.change(screen.getByLabelText("New abbreviation"), { target: { value: "STONE" } });
  fireEvent.click(screen.getByRole("button", { name: "Save catalog changes" }));
  await screen.findByText("Catalog change saved. Existing material folders and file names were not changed.");
  const call = fetch.mock.calls.find(([, init]) => init?.method === "PATCH")!;
  expect(call[0]).toBe(`/api/online-categories/${category.id}/identity`);
  expect(JSON.parse(String(call[1]?.body))).toEqual({ idempotency_key: expect.any(String), expected_version: 1, value: "Natural stone", abbreviation: "STONE" });
});

it("freezes an uncertain identity correction and retries its exact id, version and payload", async () => {
  const fetch = setup();
  fireEvent.click(await screen.findByRole("button", { name: "Edit category Stone" }));
  const dialog = screen.getByRole("dialog", { name: "Edit category" });
  fireEvent.change(screen.getByLabelText("Catalog value"), { target: { value: "Updated stone" } });
  fetch.mockRejectedValueOnce(new TypeError("Lost edit response"));
  fireEvent.click(screen.getByRole("button", { name: "Save catalog changes" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Catalog value")).toBeDisabled();
  fireEvent(dialog, new Event("cancel", { cancelable: true }));
  expect(dialog).toHaveAttribute("open");
  expect(requestNavigation("/materials")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Retry same catalog request" }));
  await screen.findByText("Catalog change saved. Existing material folders and file names were not changed.");
  const calls = fetch.mock.calls.filter(([, init]) => init?.method === "PATCH");
  expect(calls).toHaveLength(2); expect(calls[0]).toEqual(calls[1]);
  expect(requestNavigation("/materials")).toBe(true);
});

it("filters creation dates using the same local calendar day as the displayed date", async () => {
  vi.stubEnv("TZ", "Europe/Prague");
  const nearMidnight = "2026-09-25T22:30:00Z";
  const displayed = new Date(nearMidnight);
  const day = `${displayed.getFullYear()}-${String(displayed.getMonth() + 1).padStart(2, "0")}-${String(displayed.getDate()).padStart(2, "0")}`;
  setup("ADMIN", [{ ...category, created_at: nearMidnight }]);
  await screen.findByText("Stone");
  fireEvent.change(screen.getByLabelText("Created from"), { target: { value: day } });
  fireEvent.change(screen.getByLabelText("Created to"), { target: { value: day } });
  expect(screen.getByText("Stone")).toBeVisible();
  fireEvent.change(screen.getByRole("searchbox", { name: "Search catalog" }), { target: { value: "Missing" } });
  expect(screen.getByText("No catalog values match these filters.")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(screen.getByText("Stone")).toBeVisible();
});

it("does not offer bulk catalog edits even when rows are selected", async () => {
  const fetch = setup("ADMIN", [category, { ...category, id: "10000000-0000-4000-8000-000000000002", value: "Wood", abbreviation: "A" }]);
  await screen.findByText("Stone");
  fireEvent.change(screen.getByRole("searchbox", { name: "Search catalog" }), { target: { value: "Stone" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all filtered rows" }));
  expect(screen.queryByRole("combobox", { name: "Bulk property" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review bulk change" })).not.toBeInTheDocument();
  expect(fetch.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false);
});

it("preserves catalog filters, selection and the table when the window changes size", async () => {
  let resized: (() => void) | undefined;
  const media = { matches: true, addEventListener: vi.fn((_event: string, listener: () => void) => { resized = listener; }), removeEventListener: vi.fn() };
  vi.stubGlobal("matchMedia", vi.fn(() => media));
  const fetch = setup();
  await screen.findByText("Stone");
  const input = screen.getByRole("searchbox", { name: "Search catalog" });
  fireEvent.change(screen.getByRole("searchbox", { name: "Search catalog" }), { target: { value: "Stone" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all filtered rows" }));
  const results = screen.getByRole("region", { name: "Database results" });
  expect(results.parentElement).toHaveClass("database-table-viewport--contained");
  const requests = fetch.mock.calls.length;
  act(() => { media.matches = false; resized?.(); });
  expect(results.parentElement).not.toHaveClass("database-table-viewport--contained");
  expect(screen.getByLabelText("Created from")).toBeVisible();
  act(() => { media.matches = true; resized?.(); });
  expect(results.parentElement).toHaveClass("database-table-viewport--contained");
  expect(screen.getByRole("searchbox", { name: "Search catalog" })).toBe(input);
  expect(screen.getByRole("searchbox", { name: "Search catalog" })).toHaveValue("Stone");
  expect(screen.getByRole("checkbox", { name: "Select all filtered rows" })).toBeChecked();
  expect(fetch.mock.calls).toHaveLength(requests);
});

it("keeps the catalog create dialog and its draft open during responsive layout changes", async () => {
  let resized: (() => void) | undefined;
  const media = { matches: true, addEventListener: vi.fn((_event: string, listener: () => void) => { resized = listener; }), removeEventListener: vi.fn() };
  vi.stubGlobal("matchMedia", vi.fn(() => media));
  const fetch = setup(), dialog = await openCreate();
  const input = within(dialog).getByLabelText("Catalog value");
  expect(input).toHaveFocus();
  fireEvent.change(input, { target: { value: "Wood draft" } });
  act(() => { media.matches = false; resized?.(); });
  expect(screen.getByRole("dialog", { name: "Add catalog value" })).toBe(dialog);
  expect(input).toHaveValue("Wood draft");
  fireEvent(dialog, new Event("cancel", { cancelable: true }));
  expect(dialog).not.toHaveAttribute("open");
  expect(screen.getByRole("button", { name: "Add catalog value" })).toHaveFocus();
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});
