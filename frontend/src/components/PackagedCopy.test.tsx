import { randomBytes, webcrypto } from "node:crypto";
import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PackagedCopy } from "./PackagedCopy";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";
import { setSessionToken } from "../auth/sessionTransport";
import { processorDto } from "../test/materialFixtures";
import { actorId, copyJob, dispatchId, materialId, removalAction, reason, retirementDto, retirementId } from "../test/packagingRetirementFixtures";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
beforeEach(() => { vi.stubGlobal("crypto", webcrypto); });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
function tree(role: Role = "ADMIN", actor = actorId, job = copyJob, pending = false) {
  const token = randomBytes(32).toString("base64url"); setSessionToken(token);
  return <SessionContext.Provider value={{ session: { user: { ...processorDto, id: actor, role }, must_change_password: false, csrf_token: token },
    pending, logout: vi.fn(), changePassword: vi.fn() }}><PackagedCopy materialId={materialId} job={job} /></SessionContext.Provider>;
}
function server(current: ReturnType<typeof retirementDto> | null = null, enabled = true) {
  const writes: { path: string; body: string }[] = [];
  const fetch = vi.fn(async (path: string, options?: RequestInit): Promise<Response> => {
    if (options?.method === "POST") { writes.push({ path, body: String(options.body) }); current = retirementDto(); return json(current); }
    if (path.includes("/dispatches?")) return json({ items: [removalAction()], next_cursor: null });
    return json({ enabled, retirement: current });
  });
  vi.stubGlobal("fetch", fetch);
  return { fetch, writes, set: (value: ReturnType<typeof retirementDto> | null) => { current = value; } };
}
function confirm() {
  fireEvent.change(screen.getByLabelText("Reason for local copy removal"), { target: { value: reason } });
  fireEvent.click(screen.getByRole("checkbox", { name: /I reviewed this package proof/ }));
}

it("verifies availability before exposing downloads and sends only an explicitly acknowledged proof-bound removal", async () => {
  const mock = server(); render(<StrictMode>{tree()}</StrictMode>);
  expect(screen.queryByRole("button", { name: "Load packaged files" })).not.toBeInTheDocument();
  await screen.findByRole("button", { name: "Load packaged files" }); expect(mock.fetch).toHaveBeenCalledOnce();
  const remove = screen.getByRole("button", { name: "Remove this local copy" }); expect(remove).toBeDisabled();
  confirm(); fireEvent.click(remove); fireEvent.click(remove);
  await screen.findByText("Local copy removed"); expect(mock.writes).toHaveLength(1);
  expect(JSON.parse(mock.writes[0].body)).toEqual({ idempotency_key: expect.any(String), expected_observation_id: copyJob.lastObservationId,
    expected_proof_sha256: copyJob.proofSha256, acknowledgement: "REMOVE_LOCAL_COPY", reason });
  expect(screen.queryByRole("button", { name: "Load packaged files" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Recover this removal" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Load removal actions" })); await screen.findByText(/Removal requested/);
});

it.each(["RESERVED", "RUNNING", "RECOVERY_REQUIRED", "REMOVED"])("quarantines downloads when the recorded state is %s", async (status) => {
  const mock = server(retirementDto(status)); render(tree("LEADERSHIP"));
  await screen.findByText(/Downloads and new staging jobs/);
  expect(screen.queryByRole("button", { name: "Load packaged files" })).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument(); expect(mock.writes).toHaveLength(0);
});
it("leaves existing files available when removal is disabled and hides administrator actions from leadership", async () => {
  server(null, false); const mounted = render(tree());
  await screen.findByRole("button", { name: "Load packaged files" }); await screen.findByText("Local copy removal is disabled by the operator.");
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  mounted.rerender(tree("LEADERSHIP")); await screen.findByRole("button", { name: "Load packaged files" });
  expect(screen.queryByRole("button", { name: "Remove this local copy" })).not.toBeInTheDocument();
});
it.each(["PROCESSOR", "PRODUCTION_LEAD"] as Role[])("does not request copy evidence for %s", (role) => {
  const mock = server(); render(tree(role)); expect(mock.fetch).not.toHaveBeenCalled(); expect(screen.queryByRole("region")).not.toBeInTheDocument();
});
it("fails closed on unreadable initial state without reflecting raw diagnostics", async () => {
  const mock = server(); mock.fetch.mockResolvedValue(json({ detail: "PRIVATE_DIAGNOSTIC" }, 503)); render(tree());
  expect(await screen.findByRole("alert")).not.toHaveTextContent("PRIVATE_DIAGNOSTIC");
  expect(screen.queryByRole("button", { name: "Load packaged files" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Remove this local copy" })).not.toBeInTheDocument();
});
it("preserves the exact packet through a lost response and empty read, then explicitly retries it once", async () => {
  const mock = server(); const original = mock.fetch.getMockImplementation()!; const sent: string[] = [];
  mock.fetch.mockImplementation(async (path, options) => {
    if (options?.method === "POST") { sent.push(String(options.body)); if (sent.length === 1) throw new TypeError("Synthetic loss"); }
    return original(path, options);
  });
  render(tree()); await screen.findByRole("button", { name: "Load packaged files" }); confirm();
  fireEvent.click(screen.getByRole("button", { name: "Remove this local copy" })); await screen.findByText(/The removal outcome is unknown/);
  expect(screen.getByRole("textbox")).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Load packaged files" })).not.toBeInTheDocument();
  const unload = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(unload); expect(unload.defaultPrevented).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Check recorded removal" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Retry same removal request" })).toBeEnabled());
  expect(sent).toHaveLength(1); expect(screen.getByRole("textbox")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry same removal request" })); await screen.findByText("Local copy removed");
  expect(sent).toHaveLength(2); expect(sent[0]).toBe(sent[1]);
  const cleared = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(cleared); expect(cleared.defaultPrevented).toBe(false);
});
it("recovers a committed lost response by reading only and never retries automatically", async () => {
  const mock = server(), original = mock.fetch.getMockImplementation()!;
  mock.fetch.mockImplementation(async (path, options) => { const response = await original(path, options); if (options?.method === "POST") throw new TypeError("Synthetic lost receipt"); return response; });
  render(tree()); await screen.findByRole("button", { name: "Load packaged files" }); confirm(); fireEvent.click(screen.getByRole("button", { name: "Remove this local copy" }));
  await screen.findByText(/The removal outcome is unknown/); fireEvent.click(screen.getByRole("button", { name: "Check recorded removal" }));
  await screen.findByText("Local copy removed"); expect(mock.writes).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Retry same removal request" })).not.toBeInTheDocument();
});
it("binds explicit recovery to the existing intent and latest acknowledged dispatch", async () => {
  const mock = server(retirementDto("RECOVERY_REQUIRED")); render(tree());
  await screen.findByText("Local copy removal needs recovery"); confirm(); fireEvent.click(screen.getByRole("button", { name: "Recover this removal" }));
  await screen.findByText("Local copy removed");
  expect(mock.writes).toHaveLength(1); expect(mock.writes[0].path).toMatch(/\/retirement\/reconcile$/);
  expect(JSON.parse(mock.writes[0].body)).toEqual({ idempotency_key: expect.any(String), expected_retirement_id: retirementId,
    expected_last_dispatch_id: dispatchId, expected_proof_sha256: copyJob.proofSha256, acknowledgement: "REMOVE_LOCAL_COPY", reason });
});
it("preserves verified removal when a later read is missing or regresses evidence", async () => {
  const mock = server(retirementDto()); render(tree()); await screen.findByText("Local copy removed");
  mock.set(null); fireEvent.click(screen.getByRole("button", { name: "Check recorded removal" }));
  await screen.findByRole("alert"); expect(screen.getByText("Local copy removed")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Load packaged files" })).not.toBeInTheDocument();
  mock.set(retirementDto("RUNNING")); fireEvent.click(screen.getByRole("button", { name: "Check recorded removal" }));
  await screen.findByRole("alert"); expect(screen.getByText("Local copy removed")).toBeInTheDocument();
});
it("ignores a late write callback after the actor changes and requires the new actor to read current evidence", async () => {
  const mock = server(), original = mock.fetch.getMockImplementation()!;
  let finish!: (response: Response) => void;
  mock.fetch.mockImplementation((path, options) => options?.method === "POST" ? new Promise((resolve) => { finish = resolve; }) : original(path, options));
  const mounted = render(tree()); await screen.findByRole("button", { name: "Load packaged files" }); confirm(); fireEvent.click(screen.getByRole("button", { name: "Remove this local copy" }));
  mounted.rerender(tree("ADMIN", materialId)); await screen.findByRole("button", { name: "Load packaged files" });
  await act(async () => { finish(json(retirementDto())); });
  expect(screen.queryByText("Local copy removed")).not.toBeInTheDocument(); expect(screen.getByRole("textbox")).toHaveValue("");
});
it("ignores stale initial reads after the selected proof changes and avoids reloads for the same proof", async () => {
  const mock = server(); let finish!: (response: Response) => void;
  mock.fetch.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const mounted = render(tree()); await waitFor(() => expect(mock.fetch).toHaveBeenCalledOnce());
  mounted.rerender(tree("ADMIN", actorId, { ...copyJob, proofSha256: "e".repeat(64) })); await screen.findByRole("button", { name: "Load packaged files" });
  await act(async () => { finish(json({ enabled: true, retirement: retirementDto() })); });
  expect(screen.queryByText("Local copy removed")).not.toBeInTheDocument();
  mounted.rerender(tree("ADMIN", actorId, { ...copyJob, proofSha256: "e".repeat(64) }));
  await act(async () => {}); expect(mock.fetch).toHaveBeenCalledTimes(2);
});
it("blocks actions while the current session is changing", async () => {
  const mock = server(); render(tree("ADMIN", actorId, copyJob, true));
  await screen.findByRole("button", { name: "Load packaged files" });
  expect(screen.getByRole("button", { name: "Remove this local copy" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Check recorded removal" })).toBeDisabled(); expect(mock.writes).toHaveLength(0);
});
