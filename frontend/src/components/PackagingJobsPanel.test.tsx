import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PackagingJobsPanel } from "./PackagingJobsPanel";
import { packagingClient, packagingJobFromDto } from "../api/packagingClient";
import { policyValues } from "../api/packagingPolicyClient";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import type { Role } from "../auth/client";
import { materialDto, processorDto } from "../test/materialFixtures";

const id = "10000000-0000-4000-8000-000000000001", dispatchId = "10000000-0000-4000-8000-000000000002";
const batch = { id, snapshotHash: "a".repeat(64) };
const policy = { id, material_id: materialDto.id, revision: 1, previous_id: null, inventory_id: id, kind: "INITIAL",
  policy: policyValues[1], storage_timezone: "Europe/Prague", actor_id: id, reason: "Reviewed synthetic rule",
  evidence_hash: "d".repeat(64), created_at: "2026-09-18T12:00:00Z" };
function job(status = "RESERVED") {
  const observed = !["RESERVED", "RUNNING"].includes(status), packaged = status === "PACKAGED";
  return { id, material_id: materialDto.id, batch_id: id, actor_id: id, policy_id: id, status,
    input_hash: batch.snapshotHash, worker_request_hash: "b".repeat(64), last_dispatch_id: status === "RESERVED" ? null : dispatchId,
    last_observation_id: observed ? id : null, proof_sha256: packaged ? "c".repeat(64) : null,
    failure_code: status === "RECOVERY_REQUIRED" ? "PACKAGING_UNAVAILABLE" : null,
    inputs_current: observed ? packaged : null, actor_current: observed ? true : null,
    terminal: status === "REJECTED" ? "CLOSED" : ["PACKAGED", "RETRY_REQUIRED"].includes(status) ? "OPEN" : null,
    created_at: "2026-09-18T12:00:00Z", updated_at: "2026-09-18T12:00:00Z", reason: "Reviewed packaging" };
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); setSessionToken(null); });
function mount(role: Role = "ADMIN", selectedBatch = true) {
  const onChanged = vi.fn(); setSessionToken("p".repeat(43));
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "p".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <PackagingJobsPanel materialId={materialDto.id} batch={selectedBatch ? batch : undefined} onChanged={onChanged} />
  </SessionContext.Provider>);
  return onChanged;
}
function open() {
  const details = screen.getByText("Material packaging", { selector: "summary" }).parentElement as HTMLDetailsElement;
  details.open = true; fireEvent(details, new Event("toggle")); return details;
}
function confirm() {
  fireEvent.change(screen.getByLabelText("Reason for packaging action"), { target: { value: "Reviewed packaging" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "I reviewed the selected batch, ZIP rule and job progress." }));
}
function server(initial: ReturnType<typeof job> | null = null, enabled = true) {
  let current = initial;
  const writes: { path: string; options?: RequestInit }[] = [];
  const fetch = vi.fn(async (path: string, options?: RequestInit) => {
    if (options?.method === "POST") {
      writes.push({ path, options });
      current = job(path.endsWith("/run") || path.endsWith("/retry") || path.endsWith("/reconcile") ? "PACKAGED" : path.endsWith("/close") ? "REJECTED" : "RESERVED");
      return json(current);
    }
    if (path.endsWith("/packaging-policy")) return json({ current: policy });
    if (path.endsWith("/packaging-executions")) return json({ enabled, items: current ? [current] : [], next_cursor: null });
    return json(current);
  });
  vi.stubGlobal("fetch", fetch); return { fetch, writes, set: (value: ReturnType<typeof job>) => { current = value; } };
}

it("loads lazily, reserves approved inputs and requires a separate explicit start", async () => {
  const { fetch, writes } = server(); const changed = mount("LEADERSHIP");
  expect(fetch).not.toHaveBeenCalled(); open();
  const reserve = await screen.findByRole("button", { name: "Reserve packaging job" });
  expect(reserve).toBeDisabled(); confirm(); fireEvent.click(reserve);
  await screen.findByText("Job reserved. Start packaging when ready.");
  expect(writes).toHaveLength(1);
  expect(JSON.parse(String(writes[0].options?.body))).toEqual({ idempotency_key: expect.any(String), batch_id: id,
    expected_snapshot_hash: batch.snapshotHash, expected_policy_id: id, reason: "Reviewed packaging" });
  expect(writes[0].options?.headers).toEqual(expect.objectContaining({ "X-CSRF-Token": "p".repeat(43) }));
  const start = await screen.findByRole("button", { name: "Start packaging" });
  expect(start).toBeDisabled(); confirm(); fireEvent.click(start);
  await screen.findByRole("heading", { name: "Packaged" });
  expect(writes).toHaveLength(2); expect(changed).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: "Retry packaging" })).not.toBeInTheDocument();
});

it("recovers the exact lost reservation request after collapsing the panel", async () => {
  const bodies: string[] = []; let current: ReturnType<typeof job> | null = null;
  vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
    if (options?.method === "POST") {
      bodies.push(String(options.body)); current = job();
      if (bodies.length === 1) throw new TypeError("Synthetic response loss");
      return json(current);
    }
    return json(path.endsWith("/packaging-policy") ? { current: policy } : { enabled: true, items: current ? [current] : [], next_cursor: null });
  }));
  mount(); const details = open(); await screen.findByRole("button", { name: "Reserve packaging job" }); confirm();
  fireEvent.click(screen.getByRole("button", { name: "Reserve packaging job" }));
  await screen.findByText(/The outcome is unknown/);
  expect(screen.getByLabelText("Reason for packaging action")).toBeDisabled();
  details.open = false; fireEvent(details, new Event("toggle")); open();
  fireEvent.click(screen.getByRole("button", { name: "Recover same packaging request" }));
  await screen.findByText("Job reserved. Start packaging when ready.");
  expect(bodies).toHaveLength(2); expect(bodies[0]).toBe(bodies[1]);
});

it("polls the known job during a pending start without sending another command", async () => {
  const mocked = server(job()); let finish: (response: Response) => void = () => undefined;
  const original = mocked.fetch.getMockImplementation()!;
  mocked.fetch.mockImplementation((path, options) => {
    if (path.endsWith("/run")) {
      mocked.set(job("RUNNING"));
      return new Promise<Response>((resolve) => { finish = resolve; });
    }
    return original(path, options);
  });
  mount(); open(); fireEvent.click(await screen.findByRole("button", { name: /Open Reserved/ }));
  await screen.findByRole("button", { name: "Start packaging" }); confirm();
  vi.useFakeTimers(); fireEvent.click(screen.getByRole("button", { name: "Start packaging" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.getByRole("heading", { name: "Request in progress" })).toBeVisible();
  expect(mocked.fetch.mock.calls.filter(([path]) => path.endsWith("/run"))).toHaveLength(1);
  await act(async () => { mocked.set(job("PACKAGED")); finish(json(job("PACKAGED"))); });
  vi.useRealTimers(); await screen.findByRole("heading", { name: "Packaged" });
});

it.each(["RETRY_REQUIRED", "RECOVERY_REQUIRED", "RUNNING"])("offers reconciliation for %s and preserves the expected dispatch", async (state) => {
  const { writes } = server(job(state)); mount("LEADERSHIP", false); open();
  fireEvent.click(await screen.findByRole("button", { name: /^Open / }));
  await screen.findByRole("button", { name: "Check packaging result" });
  expect(screen.queryByRole("button", { name: "Close packaging job" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Retry packaging" }) !== null).toBe(state === "RETRY_REQUIRED");
  confirm(); fireEvent.click(screen.getByRole("button", { name: "Check packaging result" }));
  await screen.findByRole("heading", { name: "Packaged" });
  expect(JSON.parse(String(writes[0].options?.body)).expected_last_dispatch_id).toBe(dispatchId);
});

it("allows admin unsent closure while service is disabled", async () => {
  const { writes } = server(job(), false); mount(); open();
  fireEvent.click(await screen.findByRole("button", { name: /Open Reserved/ }));
  await screen.findByRole("button", { name: "Close packaging job" });
  expect(screen.queryByRole("button", { name: "Start packaging" })).not.toBeInTheDocument();
  confirm(); fireEvent.click(screen.getByRole("button", { name: "Close packaging job" }));
  await screen.findByRole("heading", { name: "Closed" }); expect(writes).toHaveLength(1);
});

it("requires admin closure for a closing journal and never offers conversion", async () => {
  const value = { ...job("RECOVERY_REQUIRED"), terminal: "CLOSING" }; server(value); mount(); open();
  fireEvent.click(await screen.findByRole("button", { name: /^Open / }));
  await screen.findByText(/Closure has started/);
  expect(screen.queryByRole("button", { name: "Retry packaging" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Close packaging job" })).toBeDisabled();
});

it.each(["PROCESSOR", "PRODUCTION_LEAD"] as const)("hides packaging operations from %s without requesting data", (role: Role) => {
  const { fetch } = server(); mount(role);
  expect(screen.queryByText("Material packaging")).not.toBeInTheDocument(); expect(fetch).not.toHaveBeenCalled();
});

it("rejects a stale command without reflecting diagnostics or automatically retrying", async () => {
  const mocked = server(job()); const original = mocked.fetch.getMockImplementation()!;
  mocked.fetch.mockImplementation((path, options) => options?.method === "POST"
    ? Promise.resolve(json({ detail: "PRIVATE_SYNTHETIC_DIAGNOSTIC" }, 409)) : original(path, options));
  mount(); open(); fireEvent.click(await screen.findByRole("button", { name: /^Open / }));
  await screen.findByRole("button", { name: "Start packaging" }); confirm();
  fireEvent.click(screen.getByRole("button", { name: "Start packaging" }));
  expect(await screen.findByRole("alert")).not.toHaveTextContent("PRIVATE_SYNTHETIC_DIAGNOSTIC");
  await waitFor(() => expect(screen.getByRole("checkbox")).not.toBeChecked());
  expect(screen.queryByRole("button", { name: "Recover same packaging request" })).not.toBeInTheDocument();
});

it.each([{ status: "UNKNOWN" }, { material_id: dispatchId }, { worker_request_hash: "bad" }, { last_dispatch_id: dispatchId },
  { inputs_current: true }, { terminal: "OPEN" }, { failure_code: "private text" }])("rejects malformed job %j", (change) => {
  expect(() => packagingJobFromDto({ ...job(), ...change }, materialDto.id)).toThrow();
});
it("rejects false completion and results for another execution", async () => {
  expect(() => packagingJobFromDto({ ...job("PACKAGED"), inputs_current: false }, materialDto.id)).toThrow();
  vi.stubGlobal("fetch", vi.fn(async () => json({ ...job(), id: dispatchId })));
  await expect(packagingClient.detail(materialDto.id, id)).rejects.toThrow("Wrong packaging execution");
});
