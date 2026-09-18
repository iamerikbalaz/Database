import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StagingPanel } from "./StagingPanel";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";
import { setSessionToken } from "../auth/sessionTransport";
import { publicationBatchFromDto, type PublicationBatch } from "../api/publicationClient";
import { publicationBatchDto } from "../test/publicationFixtures";
import { materialDto, processorDto } from "../test/materialFixtures";
import { stagingJobDto, stagingPreviewDto, packagedDto, stagingDispatchDto, stagingTransferDto, stagingId, stagingDispatchId, packageId, proofHash, stagingHash } from "../test/stagingFixtures";

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); setSessionToken(null); });
const batch = () => publicationBatchFromDto(publicationBatchDto());
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const reviewLabel = "I reviewed the selected CSV batch, destination and storage progress.";
function mount(role: Role = "ADMIN", selectedBatch: PublicationBatch | null = batch(), actorId = crypto.randomUUID()) {
  setSessionToken("s".repeat(43));
  const view = (value: PublicationBatch | null) => <SessionContext.Provider value={{ session: { user: { ...processorDto, id: actorId, role }, must_change_password: false, csrf_token: "s".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <StagingPanel batch={value} />
  </SessionContext.Provider>;
  const rendered = render(view(selectedBatch));
  return { ...rendered, actorId, batch: (value: PublicationBatch | null) => rendered.rerender(view(value)) };
}
function open() {
  const details = screen.getByText("Storage uploads", { selector: "summary" }).parentElement as HTMLDetailsElement;
  details.open = true; fireEvent(details, new Event("toggle")); return details;
}
function confirm() {
  fireEvent.change(screen.getByLabelText("Reason for storage action"), { target: { value: "Reviewed synthetic staging" } });
  fireEvent.click(screen.getByRole("checkbox", { name: reviewLabel }));
}
async function prepare() {
  fireEvent.click(await screen.findByRole("button", { name: `Load packages for ${materialDto.technical_identity}` }));
  fireEvent.change(await screen.findByRole("combobox", { name: `Accepted package for ${materialDto.technical_identity}` }), { target: { value: packageId } });
  fireEvent.click(screen.getByRole("button", { name: "Review storage upload" }));
  await screen.findByRole("heading", { name: "Review upload destination" });
}
function server(initial: ReturnType<typeof stagingJobDto> | null = null, enabled = true) {
  let current = initial;
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  const fetch = vi.fn(async (path: string, options?: RequestInit) => {
    if (options?.method === "POST") {
      const body = JSON.parse(String(options.body));
      if (path.endsWith("/staging-preview")) return json(stagingPreviewDto(body.job_id, enabled));
      writes.push({ path, body });
      if (path.endsWith("/run") || path.endsWith("/reconcile")) current = { ...stagingJobDto("STAGED_VERIFIED"), id: current!.id };
      else if (path.endsWith("/close") || path.endsWith("/abandon")) current = { ...stagingJobDto("CLOSED", path.endsWith("/abandon")), id: current!.id };
      else current = { ...stagingJobDto(), id: body.job_id, reason: body.reason };
      return json(current);
    }
    if (path.includes("/packaging-executions")) return json({ enabled: true, items: [packagedDto()], next_cursor: null });
    if (path.endsWith("/transfers")) return json({ items: [stagingTransferDto()], next_cursor: null });
    if (path.endsWith("/dispatches")) return json({ items: [stagingDispatchDto()], next_cursor: null });
    if (path.endsWith("/publication-staging-jobs") || path.includes("?after=")) return json({ enabled, items: current ? [current] : [], next_cursor: null });
    return json(current);
  });
  vi.stubGlobal("fetch", fetch);
  return { fetch, writes, set: (value: ReturnType<typeof stagingJobDto>) => { current = value; } };
}
it("loads lazily, reviews exact packages and reserves without automatically uploading", async () => {
  const { fetch, writes } = server(); mount("LEADERSHIP"); open();
  await screen.findByText("No storage jobs.");
  expect(fetch.mock.calls.every(([path]) => path.endsWith("/publication-staging-jobs"))).toBe(true);
  expect(screen.getByRole("button", { name: "Review storage upload" })).toBeDisabled();
  await prepare(); expect(screen.getByRole("button", { name: "Reserve storage job" })).toBeDisabled();
  expect(screen.getByText(/gs:\/\/synthetic-reawote-staging/)).toBeVisible(); confirm();
  fireEvent.click(screen.getByRole("button", { name: "Reserve storage job" }));
  await screen.findByText("Storage job reserved. Review its destination before starting upload.");
  expect(writes).toHaveLength(1); expect(writes[0].body).toEqual(expect.objectContaining({ job_id: expect.any(String), expected_plan_sha256: stagingHash,
    batch_id: batch().id, expected_snapshot_hash: batch().snapshotHash, expected_csv_sha256: batch().csvSha256, reason: "Reviewed synthetic staging",
    packages: [{ material_id: materialDto.id, execution_id: packageId, expected_observation_id: expect.any(String), expected_proof_sha256: proofHash }] }));
  expect(await screen.findByRole("button", { name: "Start storage upload" })).toBeDisabled();
});
it("retains an uncertain reservation across batch changes, collapse and in-app remount", async () => {
  const mocked = server(); const original = mocked.fetch.getMockImplementation()!; let lost = false;
  mocked.fetch.mockImplementation(async (path, options) => { const response = await original(path, options);
    if (options?.method === "POST" && path.endsWith("/publication-staging-jobs") && !lost) { lost = true; throw new TypeError("Synthetic loss"); } return response; });
  const view = mount(); const details = open(); await prepare(); confirm();
  fireEvent.click(screen.getByRole("button", { name: "Reserve storage job" })); await screen.findByText(/The outcome is unknown/);
  view.batch({ ...batch(), id: stagingDispatchId });
  details.open = false; fireEvent(details, new Event("toggle")); open();
  expect(screen.getByRole("button", { name: "Review storage upload" })).toBeDisabled();
  view.unmount(); mount("ADMIN", null, view.actorId);
  fireEvent.click(await screen.findByRole("button", { name: "Recover same storage request" }));
  await screen.findByText("Storage job reserved. Review its destination before starting upload.");
  expect(mocked.writes).toHaveLength(2); expect(mocked.writes[0]).toEqual(mocked.writes[1]);
  expect(mocked.writes[1].body.batch_id).toBe(batch().id);
});
it("requires separate explicit upload and polls during the pending request", async () => {
  const mocked = server(stagingJobDto()); const original = mocked.fetch.getMockImplementation()!;
  let complete: (response: Response) => void = () => undefined;
  mocked.fetch.mockImplementation((path, options) => {
    if (path.endsWith("/run")) { mocked.set(stagingJobDto("RUNNING")); return new Promise<Response>((resolve) => { complete = resolve; }); }
    return original(path, options);
  });
  mount(); open(); fireEvent.click(await screen.findByRole("button", { name: /^Open Reserved/ }));
  await screen.findByRole("heading", { name: "Reserved · ready to upload" }); confirm();
  vi.useFakeTimers(); fireEvent.click(screen.getByRole("button", { name: "Start storage upload" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.getByRole("heading", { name: "Transfer in progress" })).toBeVisible();
  expect(mocked.fetch.mock.calls.filter(([path]) => path.endsWith("/run"))).toHaveLength(1);
  await act(async () => { mocked.set(stagingJobDto("STAGED_VERIFIED")); complete(json(stagingJobDto("STAGED_VERIFIED"))); });
  vi.useRealTimers(); await screen.findByRole("heading", { name: "Storage verified" });
  expect(screen.getByText(/Online import and publication remain separate/)).toBeVisible();
});
it.each(["RUNNING", "RECOVERY_REQUIRED", "STAGED_VERIFIED"])("only offers read-only recovery for dispatched %s", async (status) => {
  const { writes } = server(stagingJobDto(status)); mount("LEADERSHIP", null); open();
  fireEvent.click(await screen.findByRole("button", { name: /^Open / })); await screen.findByRole("button", { name: "Check stored files" });
  expect(screen.queryByRole("button", { name: "Start storage upload" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Close dispatched storage job" })).not.toBeInTheDocument();
  confirm(); fireEvent.click(screen.getByRole("button", { name: "Check stored files" }));
  await screen.findByText("Storage job progress updated.");
  expect(writes).toHaveLength(1); expect(writes[0].path).toContain("/reconcile"); expect(writes[0].body.expected_last_dispatch_id).toBe(stagingDispatchId);
});
it("does not let a delayed progress read replace the completed command", async () => {
  const mocked = server(stagingJobDto()); const original = mocked.fetch.getMockImplementation()!;
  let complete: (response: Response) => void = () => undefined, releaseRead: (response: Response) => void = () => undefined;
  let started = false;
  mocked.fetch.mockImplementation((path, options) => {
    if (path.endsWith("/run")) { started = true; return new Promise<Response>((resolve) => { complete = resolve; }); }
    if (started && path.endsWith("/" + stagingId)) return new Promise<Response>((resolve) => { releaseRead = resolve; });
    return original(path, options);
  });
  mount(); open(); fireEvent.click(await screen.findByRole("button", { name: /^Open / }));
  await screen.findByRole("button", { name: "Start storage upload" }); confirm();
  vi.useFakeTimers(); fireEvent.click(screen.getByRole("button", { name: "Start storage upload" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  await act(async () => { mocked.set(stagingJobDto("STAGED_VERIFIED")); complete(json(stagingJobDto("STAGED_VERIFIED"))); });
  await act(async () => { releaseRead(json(stagingJobDto("RUNNING"))); });
  vi.useRealTimers(); expect(await screen.findByRole("heading", { name: "Storage verified" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Transfer in progress" })).not.toBeInTheDocument();
});
it("permits leadership to close an unsent job with cloud transfer disabled", async () => {
  const { writes } = server(stagingJobDto(), false); mount("LEADERSHIP", null); open();
  fireEvent.click(await screen.findByRole("button", { name: /^Open / })); await screen.findByRole("heading", { name: "Reserved · ready to upload" });
  expect(screen.getByText(/Storage upload is disabled/)).toBeVisible(); expect(screen.queryByRole("button", { name: "Start storage upload" })).not.toBeInTheDocument();
  confirm(); fireEvent.click(screen.getByRole("button", { name: "Close unsent storage job" }));
  await screen.findByRole("heading", { name: "Closed" });
  expect(writes[0].body).toEqual({ idempotency_key: expect.any(String), expected_plan_sha256: stagingHash, reason: "Reviewed synthetic staging" });
});
it("requires admin's separate late-effects acknowledgment to abandon a dispatched job", async () => {
  const { writes } = server(stagingJobDto("RECOVERY_REQUIRED"), false); mount("ADMIN", null); open();
  fireEvent.click(await screen.findByRole("button", { name: /^Open / }));
  const close = await screen.findByRole("button", { name: "Close dispatched storage job" }); confirm(); expect(close).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "I acknowledge possible late remote effects and retained objects." })); fireEvent.click(close);
  await screen.findByRole("heading", { name: "Closed" }); expect(writes[0].body.acknowledge_possible_remote_effects).toBe(true);
  expect(writes[0].body.expected_last_dispatch_id).toBe(stagingDispatchId); expect(screen.getByText(/does not cancel remote requests/)).toBeVisible();
});
it("shows receipt evidence and exact large generation strings", async () => {
  server(stagingJobDto("STAGED_VERIFIED")); mount(); open(); fireEvent.click(await screen.findByRole("button", { name: /^Open / }));
  fireEvent.click(await screen.findByRole("button", { name: "Load storage actions" }));
  fireEvent.click(await screen.findByRole("button", { name: "Files for action 1" }));
  expect(await screen.findByText(/generation 9007199254740993/)).toBeVisible();
});
it.each(["PROCESSOR", "PRODUCTION_LEAD"] as const)("hides storage operations from %s without requests", (role) => {
  const { fetch } = server(); mount(role); expect(screen.queryByText("Storage uploads")).not.toBeInTheDocument(); expect(fetch).not.toHaveBeenCalled();
});
it("rejects a changed request without reflecting remote diagnostics or auto-retrying", async () => {
  const mocked = server(stagingJobDto()); const original = mocked.fetch.getMockImplementation()!;
  mocked.fetch.mockImplementation((path, options) => options?.method === "POST" ? Promise.resolve(json({ detail: "PRIVATE_SYNTHETIC_DIAGNOSTIC" }, 409)) : original(path, options));
  mount(); open(); fireEvent.click(await screen.findByRole("button", { name: /^Open / })); await screen.findByRole("button", { name: "Start storage upload" });
  confirm(); fireEvent.click(screen.getByRole("button", { name: "Start storage upload" }));
  expect(await screen.findByRole("alert")).not.toHaveTextContent("PRIVATE_SYNTHETIC_DIAGNOSTIC");
  await waitFor(() => expect(screen.getByRole("checkbox", { name: reviewLabel })).not.toBeChecked());
  expect(screen.queryByRole("button", { name: "Recover same storage request" })).not.toBeInTheDocument();
});
it("drops the prior preview after a package selection changes", async () => {
  server(); mount(); open(); await prepare(); confirm();
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "" } });
  expect(screen.queryByRole("heading", { name: "Review upload destination" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Reserve storage job" })).not.toBeInTheDocument();
});
it("rejects a bad successful response as uncertain and recovers its exact command", async () => {
  const mocked = server(stagingJobDto()); const original = mocked.fetch.getMockImplementation()!; let first = true;
  mocked.fetch.mockImplementation(async (path, options) => { const value = await original(path, options);
    if (path.endsWith("/run") && first) { first = false; return json({ ...stagingJobDto("STAGED_VERIFIED"), plan_sha256: "0".repeat(64) }); } return value; });
  mount(); open(); fireEvent.click(await screen.findByRole("button", { name: /^Open / })); await screen.findByRole("button", { name: "Start storage upload" });
  confirm(); fireEvent.click(screen.getByRole("button", { name: "Start storage upload" })); await screen.findByText(/The outcome is unknown/);
  fireEvent.click(screen.getByRole("button", { name: "Recover same storage request" })); await screen.findByText("Storage job progress updated.");
  expect(mocked.writes).toHaveLength(2); expect(mocked.writes[0]).toEqual(mocked.writes[1]);
});
it("rejects packages for a different batch and does not auto-walk history", async () => {
  const mocked = server(); const original = mocked.fetch.getMockImplementation()!;
  mocked.fetch.mockImplementation((path, options) => path.includes("/packaging-executions") ? Promise.resolve(json({ enabled: true, items: [{ ...packagedDto(), batch_id: stagingId }], next_cursor: packageId })) : original(path, options));
  mount(); open(); fireEvent.click(await screen.findByRole("button", { name: `Load packages for ${materialDto.technical_identity}` }));
  await screen.findByText(/No accepted package for this batch/);
  expect(screen.getByRole("button", { name: "Review storage upload" })).toBeDisabled();
  expect(mocked.fetch.mock.calls.filter(([path]) => path.includes("packaging-executions"))).toHaveLength(1);
});
