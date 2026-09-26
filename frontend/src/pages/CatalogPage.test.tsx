import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
      return json({ ...row, version: row.version + 1, ...(payload.is_active !== undefined ? { is_active: payload.is_active } : { abbreviation: payload.abbreviation }) });
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
it("creates a brand collection with an explicit selected brand", async () => {
  const fetch = setup(); await screen.findByRole("button", { name: "Create catalog value" });
  fireEvent.change(screen.getByRole("combobox", { name: "Value type" }), { target: { value: "collections" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Collection brand" }), { target: { value: materialBrand.id } });
  fireEvent.change(screen.getByLabelText("Catalog value"), { target: { value: "Studio" } });
  fireEvent.click(screen.getByRole("button", { name: "Create catalog value" })); await screen.findByText("Catalog change saved.");
  const call = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(call[0]).toBe("/api/collections"); expect(JSON.parse(String(call[1]?.body))).toEqual({ idempotency_key: expect.any(String), value: "Studio", brand_id: materialBrand.id });
});
it("changes availability inline with a reason and exact catalog version", async () => {
  const fetch = setup(); await screen.findByRole("combobox", { name: "Active for Stone" });
  fireEvent.change(screen.getByLabelText("Reason for catalog change"), { target: { value: "Replace duplicate category" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Active for Stone" }), { target: { value: "false" } });
  await screen.findByText("Saved. Refresh to reapply filters.");
  const call = fetch.mock.calls.find(([, init]) => init?.method === "PATCH")!;
  expect(JSON.parse(String(call[1]?.body))).toEqual({ idempotency_key: expect.any(String), expected_version: 1, is_active: false, reason: "Replace duplicate category" });
  expect(call[0]).toBe(`/api/online-categories/${category.id}/table`);
});
it("replays an unknown catalog mutation with its original key and frozen inputs", async () => {
  const fetch = setup(); await screen.findByRole("button", { name: "Create catalog value" });
  fireEvent.change(screen.getByLabelText("Catalog value"), { target: { value: "Wood" } }); fetch.mockRejectedValueOnce(new TypeError("Synthetic timeout"));
  fireEvent.click(screen.getByRole("button", { name: "Create catalog value" })); await screen.findByRole("alert");
  expect(requestNavigation("/materials")).toBe(false);
  expect(screen.getByLabelText("Catalog value")).toBeDisabled(); fireEvent.click(screen.getByRole("button", { name: "Retry same catalog request" }));
  await waitFor(() => expect(screen.getByText("Catalog change saved.")).toBeVisible());
  expect(requestNavigation("/materials")).toBe(true);
  const calls = fetch.mock.calls.filter(([, init]) => init?.method === "POST"); expect(calls).toHaveLength(2); expect(calls[0][1]?.body).toBe(calls[1][1]?.body);
});
it.each(["PROCESSOR", "LEADERSHIP"] as const)("keeps %s catalog access read-only", async (role) => {
  setup(role); await screen.findByText("Stone"); expect(screen.queryByRole("button", { name: "Create catalog value" })).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: "Active for Stone" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Create replacement for Stone" })).not.toBeInTheDocument();
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
  expect(screen.getByRole("combobox", { name: "Brand filter" })).toBeVisible();
});

it("edits abbreviations inline and keeps canonical names behind explicit replacement", async () => {
  const fetch = setup(); await screen.findByRole("textbox", { name: "Abbreviation for Stone" });
  fireEvent.change(screen.getByRole("textbox", { name: "Abbreviation for Stone" }), { target: { value: "STONE" } });
  fireEvent.click(screen.getByRole("button", { name: "Save abbreviation" }));
  await screen.findByText("Saved. Refresh to reapply filters.");
  expect(JSON.parse(String(fetch.mock.calls.find(([, init]) => init?.method === "PATCH")![1]?.body))).toMatchObject({ expected_version: 1, abbreviation: "STONE" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Create replacement for Stone" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Create replacement for Stone" }));
  expect(screen.getByRole("heading", { name: "Create replacement for Stone" })).toBeVisible();
  expect(screen.getByLabelText("Catalog value")).toHaveValue("Stone");
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

it("confirms bulk activity only for the current filtered catalog values", async () => {
  const fetch = setup("ADMIN", [category, { ...category, id: "10000000-0000-4000-8000-000000000002", value: "Wood", abbreviation: "A" }]);
  await screen.findByText("Stone");
  fireEvent.change(screen.getByRole("searchbox", { name: "Search catalog" }), { target: { value: "Stone" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Bulk property" }), { target: { value: "is_active" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Bulk value" }), { target: { value: "false" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply to all 1 filtered" }));
  expect(fetch.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false);
  expect(screen.getByRole("searchbox", { name: "Search catalog" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm changes" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Close" })).toBeEnabled());
  const calls = fetch.mock.calls.filter(([, init]) => init?.method === "PATCH");
  expect(calls).toHaveLength(1); expect(calls[0][0]).toContain(category.id);
});
