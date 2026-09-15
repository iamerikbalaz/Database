import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MaterialReviewPanel } from "./MaterialReviewPanel";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import type { Role } from "../auth/client";
import { materialFromDto } from "../api/materialDto";
import { materialDto, processorDto } from "../test/materialFixtures";

const empty = { generation: 0, revision_hash: null, inventory_id: null, checked_at: null, failure_code: null };
const observed = { ...empty, generation: 1, revision_hash: "a".repeat(64), inventory_id: "10000000-0000-4000-8000-000000000001", checked_at: "2026-09-15T12:00:00Z" };
const material = materialFromDto({ ...materialDto, folder_path: "library/LASVIT_9999_G03", workflow_status: "DONE" });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
function mount(role: Role = "ADMIN", linked = true) {
  const onReopened = vi.fn(async () => true); const onScanned = vi.fn(async () => true);
  setSessionToken("t".repeat(43));
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialReviewPanel material={{ ...material, folderPath: linked ? material.folderPath : null }} onReopened={onReopened} onScanned={onScanned} />
  </SessionContext.Provider>);
  return { onReopened, onScanned };
}
function reads(path: string) { return json(path.endsWith("/audit") ? [] : { review: empty, inventory: null }); }

it("loads the observed state and sends a CSRF-protected scan with a unique key", async () => {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => init?.method === "POST" ? json(observed) : reads(path));
  vi.stubGlobal("fetch", fetch); const callbacks = mount();
  fireEvent.click(await screen.findByRole("button", { name: "Scan source inventory" }));
  await screen.findByText(/Source inventory saved/);
  const mutation = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(String(mutation[1]?.body))).toEqual({ expected_generation: 0, idempotency_key: expect.stringMatching(/^[a-f0-9-]{36}$/) });
  expect(mutation[1]?.headers).toEqual(expect.objectContaining({ "X-CSRF-Token": "t".repeat(43) }));
  expect(callbacks.onScanned).toHaveBeenCalledOnce();
});

it("keeps the idempotency key after an unknown transport outcome", async () => {
  const mutations: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      mutations.push(String(init.body));
      if (mutations.length === 1) throw new TypeError("Network unavailable");
      return json(observed);
    }
    return reads(path);
  }));
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Scan source inventory" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Scan source inventory" }));
  await screen.findByText(/Source inventory saved/);
  expect(mutations).toHaveLength(2); expect(mutations[0]).toBe(mutations[1]);
});

it("requires an explicit reason before reopening and refreshes the material", async () => {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => init?.method === "POST" ? json({ workflow_status: "IN_PROGRESS", review: empty }) : reads(path));
  vi.stubGlobal("fetch", fetch); const { onReopened } = mount();
  fireEvent.click(await screen.findByRole("button", { name: "Reopen material" }));
  expect(screen.getByRole("button", { name: "Confirm reopen" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Reason for reopening"), { target: { value: "Correct the source maps" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm reopen" }));
  await screen.findByText(/Material reopened\. Previous/);
  expect(onReopened).toHaveBeenCalledOnce();
  const mutation = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(String(mutation[1]?.body))).toEqual(expect.objectContaining({ reason: "Correct the source maps", expected_generation: 0 }));
});

it.each(["PROCESSOR", "LEADERSHIP"] as const)("presents only the allowed %s controls", async (role) => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => reads(path))); mount(role);
  await screen.findByText("A new scan is required");
  expect(screen.queryByRole("button", { name: "Reopen material" })).not.toBeInTheDocument();
  expect(Boolean(screen.queryByRole("button", { name: "Scan source inventory" }))).toBe(role === "PROCESSOR");
});

it("requires a linked folder before scanning", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => reads(path))); mount("ADMIN", false);
  expect(await screen.findByRole("button", { name: "Scan source inventory" })).toBeDisabled();
});

it("reloads invalidation state after a rejected scan without reflecting server data", async () => {
  let state = { ...empty, failure_code: null as string | null };
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") { state = { ...empty, generation: 2, failure_code: "INVENTORY_SOURCE_CHANGED" }; return json({ detail: "SYNTHETIC_PRIVATE_DATA" }, 422); }
    return json(path.endsWith("/audit") ? [] : { review: state, inventory: null });
  }));
  const { onScanned } = mount(); fireEvent.click(await screen.findByRole("button", { name: "Scan source inventory" }));
  await screen.findByText("INVENTORY_SOURCE_CHANGED");
  expect(screen.getByRole("alert")).not.toHaveTextContent("SYNTHETIC_PRIVATE_DATA");
  await waitFor(() => expect(onScanned).toHaveBeenCalledOnce());
});
