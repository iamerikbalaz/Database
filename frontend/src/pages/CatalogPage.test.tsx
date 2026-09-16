import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CatalogPage } from "./CatalogPage";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import type { Role } from "../auth/client";
import { httpApiClient } from "../api/client";
import { materialBrand, processorDto } from "../test/materialFixtures";

const category = { id: "10000000-0000-4000-8000-000000000001", value: "Stone", version: 1, is_active: true };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
function setup(role: Role = "ADMIN") {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.endsWith("/brands")) return json([materialBrand]);
    if (init?.method === "POST" || init?.method === "PATCH") return json(category);
    if (path.endsWith("/collections")) return json([]);
    return json([category]);
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
it("requires a reason and confirmation to retire a versioned category", async () => {
  const fetch = setup(); fireEvent.click(await screen.findByRole("button", { name: "Deactivate Stone" }));
  const confirm = screen.getByRole("button", { name: "Confirm availability change" }); expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Reason for catalog change"), { target: { value: "Replace duplicate category" } });
  fireEvent.click(confirm); await screen.findByText("Catalog change saved.");
  const call = fetch.mock.calls.find(([, init]) => init?.method === "PATCH")!;
  expect(JSON.parse(String(call[1]?.body))).toEqual({ idempotency_key: expect.any(String), expected_version: 1, is_active: false, reason: "Replace duplicate category" });
});
it("replays an unknown catalog mutation with its original key and frozen inputs", async () => {
  const fetch = setup(); await screen.findByRole("button", { name: "Create catalog value" });
  fireEvent.change(screen.getByLabelText("Catalog value"), { target: { value: "Wood" } }); fetch.mockRejectedValueOnce(new TypeError("Synthetic timeout"));
  fireEvent.click(screen.getByRole("button", { name: "Create catalog value" })); await screen.findByRole("alert");
  expect(screen.getByLabelText("Catalog value")).toBeDisabled(); fireEvent.click(screen.getByRole("button", { name: "Retry same catalog request" }));
  await waitFor(() => expect(screen.getByText("Catalog change saved.")).toBeVisible());
  const calls = fetch.mock.calls.filter(([, init]) => init?.method === "POST"); expect(calls).toHaveLength(2); expect(calls[0][1]?.body).toBe(calls[1][1]?.body);
});
it.each(["PROCESSOR", "LEADERSHIP"] as const)("keeps %s catalog access read-only", async (role) => {
  setup(role); await screen.findByText("Stone"); expect(screen.queryByRole("button", { name: "Create catalog value" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Deactivate Stone" })).not.toBeInTheDocument();
});
