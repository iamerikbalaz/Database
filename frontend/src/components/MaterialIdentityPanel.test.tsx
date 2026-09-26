import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { requestNavigation } from "../navigationGuard";
import { MaterialIdentityPanel } from "./MaterialIdentityPanel";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import type { Role } from "../auth/client";
import { httpApiClient } from "../api/client";
import { materialFromDto } from "../api/materialDto";
import { materialBrand, materialDto, processorDto } from "../test/materialFixtures";

const id = "10000000-0000-4000-8000-000000000001";
const source = { ...materialDto, material_id: materialDto.id, folder_path: "library/LASVIT_9999_G03" };
const target = { ...source, technical_identity: "LASVIT_9999_G02", folder_path: "library/LASVIT_9999_G02", main_category_code: "G02" };
function plan(warning = false, blocked = false) {
  return { source_context: source, target_context: target, generation: 4, reserves_number: false, proposal_hash: "b".repeat(64),
    worker_plan: { schema_version: 1, planner_version: "identity-plan-1", plan_hash: "a".repeat(64), source_revision_hash: "c".repeat(64),
      source_path: source.folder_path, target_path: target.folder_path, ready: !blocked,
      errors: blocked ? [{ code: "IDENTITY_TARGET_COLLISION", path: target.folder_path }] : [],
      warnings: warning ? [{ code: "SOURCE_METADATA_MISSING", path: "metadata.txt" }] : [],
      changes: [{ kind: "file", source: "4K/LASVIT_9999_G03_COL_4K.png", target: "4K/LASVIT_9999_G02_COL_4K.png", sha256: "e".repeat(64) }],
      metadata: { before_hash: null, after_hash: null, changed_fields: [] } } };
}
function operation(status = "COMPLETED") {
  return { id, material_id: materialDto.id, actor_id: processorDto.id, status, source_context: source, target_context: target,
    reason: "Correct category", result: status === "RUNNING" ? null : { failure_code: null }, created_at: "2026-09-16T12:00:00Z" };
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
function setup(options: { role?: Role; enabled?: boolean; warning?: boolean; blocked?: boolean; outcome?: string; published?: boolean; done?: boolean; history?: ReturnType<typeof operation>[] } = {}) {
  const changed = vi.fn(async () => true);
  const calls: { path: string; init?: RequestInit }[] = [];
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (path.endsWith("/brands")) return json([materialBrand]);
    if (path.endsWith("/identity-plan")) return json(plan(options.warning, options.blocked));
    if (path.endsWith("/identity-confirm") || path.endsWith("/resume")) return json(operation());
    return json({ mutations_enabled: options.enabled ?? true, operations: options.history ?? (options.outcome ? [operation(options.outcome)] : []) });
  });
  vi.stubGlobal("fetch", fetch); setSessionToken("t".repeat(43));
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: options.role ?? "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialIdentityPanel material={materialFromDto({ ...materialDto, folder_path: source.folder_path, is_published: options.published ?? false, workflow_status: options.done ? "DONE" : "IN_PROGRESS" })} client={httpApiClient} onChanged={changed} />
  </SessionContext.Provider>);
  return { calls, fetch, changed };
}
async function preview() {
  await screen.findByRole("button", { name: "Preview identity changes" });
  fireEvent.change(screen.getByLabelText("Target category code"), { target: { value: "G02" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview identity changes" }));
  await screen.findByRole("region", { name: "Identity change preview" });
}

it("keeps the current active operation while browsing an older finished page", async () => {
  const rows = [operation("RUNNING"), ...Array.from({ length: 99 }, (_, index) => ({ ...operation(), id: `20000000-0000-4000-8000-${String(index).padStart(12, "0")}` }))];
  const { fetch, changed } = setup({ history: rows });
  await screen.findByRole("button", { name: "Reconcile recorded operation" });
  fetch.mockResolvedValueOnce(json({ mutations_enabled: false, operations: [{ ...operation(), reason: "Older completed operation" }] }));
  fireEvent.click(screen.getByRole("button", { name: "Older identity history" }));
  await screen.findByText(/Older completed operation/);
  expect(screen.getByRole("button", { name: "Reconcile recorded operation" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Preview identity changes" })).not.toBeInTheDocument();
  expect(changed).not.toHaveBeenCalled();
  expect(fetch.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
});

it("shows exact paths and requires reason and warning acknowledgment before confirmation", async () => {
  const { calls, changed } = setup({ warning: true }); await preview();
  const confirm = screen.getByRole("button", { name: "Confirm identity change" });
  expect(confirm).toBeDisabled(); expect(screen.getByText(target.folder_path)).toBeVisible();
  fireEvent.change(screen.getByLabelText("Reason for identity change"), { target: { value: "Correct category" } });
  expect(confirm).toBeDisabled(); fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(confirm);
  await screen.findByText("Identity updated.");
  const body = calls.find((call) => call.path.endsWith("/identity-confirm"))!;
  expect(JSON.parse(String(body.init?.body))).toEqual(expect.objectContaining({ expected_generation: 4,
    expected_proposal_hash: "b".repeat(64), main_category_code: "G02", reason: "Correct category", warnings_acknowledged: true }));
  expect(body.init?.headers).toEqual(expect.objectContaining({ "X-CSRF-Token": "t".repeat(43) }));
  expect(changed).toHaveBeenCalledOnce();
});

it.each(["PROCESSOR", "LEADERSHIP"] as const)("keeps identity mutations unavailable for %s", async (role) => {
  setup({ role }); await screen.findByRole("button", { name: "Reload identity status" });
  expect(screen.queryByRole("button", { name: "Preview identity changes" })).not.toBeInTheDocument();
});

it("allows read-only preview when source writes are disabled", async () => {
  setup({ enabled: false }); await preview();
  expect(screen.getByText(/Source changes are disabled/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Confirm identity change" })).not.toBeInTheDocument();
});

it("blocks confirmation of a colliding plan and invalidates preview after an input edit", async () => {
  setup({ blocked: true }); await preview();
  expect(screen.getByRole("region", { name: "Blocking identity findings" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Confirm identity change" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Destination parent folder"), { target: { value: "other" } });
  expect(screen.queryByRole("region", { name: "Identity change preview" })).not.toBeInTheDocument();
});

it("retries an unknown confirmation with the identical key and immutable payload", async () => {
  const { fetch } = setup(); await preview();
  fireEvent.change(screen.getByLabelText("Reason for identity change"), { target: { value: "Correct category" } });
  fetch.mockImplementationOnce(async () => { expect(requestNavigation("/projects")).toBe(false); throw new TypeError("Network interrupted"); });
  fireEvent.click(screen.getByRole("button", { name: "Confirm identity change" }));
  await screen.findByRole("alert");
  expect(requestNavigation("/projects")).toBe(false);
  expect(screen.getByLabelText("Target category code")).toBeDisabled();
  expect(screen.getByLabelText("Reason for identity change")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry same confirmation" }));
  await screen.findByText("Identity updated.");
  const confirmations = fetch.mock.calls.filter(([path]) => path.endsWith("/identity-confirm"));
  expect(confirmations).toHaveLength(2); expect(confirmations[0][1]?.body).toBe(confirmations[1][1]?.body);
  expect(requestNavigation("/projects")).toBe(true);
});

it.each(["RUNNING", "RECOVERY_REQUIRED"])("shows and reconciles durable %s operation", async (outcome) => {
  const { calls } = setup({ outcome });
  fireEvent.click(await screen.findByRole("button", { name: "Reconcile recorded operation" }));
  await waitFor(() => expect(calls.some((call) => call.path.endsWith(`/identity-operations/${id}/resume`))).toBe(true));
  expect(calls.some((call) => call.path.endsWith("/identity-confirm"))).toBe(false);
});

it.each([{ published: true }, { done: true }])("prevents identity planning for an unsupported lifecycle state", async (options) => {
  setup(options); await screen.findByRole("button", { name: "Reload identity status" });
  expect(screen.queryByRole("button", { name: "Preview identity changes" })).not.toBeInTheDocument();
});
