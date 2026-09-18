import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PackagingPolicyPanel } from "./PackagingPolicyPanel";
import { packagingPolicyClient, policyFromDto, policyLabel, policyValues } from "../api/packagingPolicyClient";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import type { Role } from "../auth/client";
import { materialDto, processorDto } from "../test/materialFixtures";

const id = "10000000-0000-4000-8000-000000000001", nextId = "10000000-0000-4000-8000-000000000002";
const review = { generation: 1, revision_hash: "a".repeat(64), inventory_id: id, checked_at: "2026-09-18T12:00:00Z", failure_code: null };
const rule = (revision = 1) => ({ id: revision === 1 ? id : nextId, material_id: materialDto.id, revision,
  previous_id: revision === 1 ? null : id, inventory_id: revision === 1 ? id : null, kind: revision === 1 ? "INITIAL" : "OVERRIDE",
  policy: policyValues[revision === 1 ? 1 : 0], storage_timezone: "Europe/Prague", actor_id: id,
  reason: "Reviewed historical dates", evidence_hash: "b".repeat(64), created_at: review.checked_at });
const technical = { review, validation: { id, actor_id: id, generation: 1, revision_hash: review.revision_hash, report_hash: "b".repeat(64),
  created_at: review.checked_at, report: { schema_version: 1, validator_version: "pbr-images-1", can_approve: true, images: [], errors: [], warnings: [] } }, approvals: [] };
const observed = { review, inventory: { policy: policyValues[1], master_resolution: "4K", master_last_modified_at: review.checked_at } };
const preview = { material_id: materialDto.id, technical_identity: materialDto.technical_identity,
  current: { id, policy: policyValues[1], revision: 1, storage_timezone: "Europe/Prague" }, proposed_policy: policyValues[0],
  review, is_published: true, effects: { invalidate_current_approvals: true, preserve_existing_artifacts: true,
    published_update_required: true, source_files_modified: false }, preview_hash: "c".repeat(64) };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
function mount(role: Role = "ADMIN", done = true) {
  const onChanged = vi.fn(); setSessionToken("t".repeat(43));
  const result = render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <PackagingPolicyPanel materialId={materialDto.id} workflowStatus={done ? "DONE" : "IN_PROGRESS"} refreshVersion={0} onChanged={onChanged} />
  </SessionContext.Provider>);
  return { ...result, onChanged };
}
function open() {
  const details = screen.getByText("ZIP packaging policy", { selector: "summary" }).parentElement as HTMLDetailsElement;
  details.open = true; fireEvent(details, new Event("toggle"));
  return details;
}
function confirmReason() {
  fireEvent.change(screen.getByLabelText("Reason for ZIP policy decision"), { target: { value: "Reviewed synthetic rule" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "I reviewed this ZIP rule and its effect." }));
}

it("loads only when opened and saves the exact reviewed source with CSRF", async () => {
  let saved = false;
  const fetch = vi.fn(async (path: string, options?: RequestInit) => {
    if (path.endsWith("/select")) { expect(options?.method).toBe("POST"); saved = true; return json({ current: rule() }); }
    if (path.endsWith("/inventory")) return json(observed);
    if (path.endsWith("/technical-review")) return json(technical);
    return json({ current: saved ? rule() : null });
  });
  vi.stubGlobal("fetch", fetch); const { onChanged } = mount("LEADERSHIP");
  expect(fetch).not.toHaveBeenCalled(); open();
  const submit = await screen.findByRole("button", { name: "Save ZIP policy" });
  expect(submit).toBeDisabled(); confirmReason(); fireEvent.click(submit);
  await screen.findByText("ZIP policy saved.");
  expect(onChanged).toHaveBeenCalledOnce();
  const [, options] = fetch.mock.calls.find(([path]) => path.endsWith("/select"))!;
  expect(JSON.parse(String(options?.body))).toEqual({ idempotency_key: expect.any(String), expected_generation: 1,
    expected_revision_hash: review.revision_hash, expected_inventory_id: id, reason: "Reviewed synthetic rule" });
  expect(options?.headers).toEqual(expect.objectContaining({ "X-CSRF-Token": "t".repeat(43) }));
});

it("reviews override impact and replays the identical packet after a lost response and collapsed panel", async () => {
  const bodies: string[] = []; let saved = false;
  vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
    if (path.endsWith("/override-preview")) return json(preview);
    if (path.endsWith("/override")) {
      bodies.push(String(options?.body)); saved = true;
      if (bodies.length === 1) throw new TypeError("Synthetic lost response");
      return json({ current: rule(2) });
    }
    return json({ current: rule(saved ? 2 : 1) });
  }));
  const { onChanged } = mount(); const details = open();
  fireEvent.click(await screen.findByRole("button", { name: "Review ZIP policy change" }));
  await screen.findByRole("region", { name: "ZIP policy change preview" });
  expect(screen.getByText(/stays published/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Confirm ZIP policy change" })).toBeDisabled();
  confirmReason(); fireEvent.click(screen.getByRole("button", { name: "Confirm ZIP policy change" }));
  await screen.findByText(/The outcome is unknown/);
  expect(screen.getByLabelText("Reason for ZIP policy decision")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Cancel policy change" })).toBeDisabled();
  details.open = false; fireEvent(details, new Event("toggle")); open();
  fireEvent.click(screen.getByRole("button", { name: "Retry same policy request" }));
  await screen.findByText("ZIP policy saved.");
  expect(bodies).toHaveLength(2); expect(bodies[0]).toBe(bodies[1]); expect(onChanged).toHaveBeenCalledOnce();
});

it("clears a rejected preview and hides server diagnostics", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => path.endsWith("/override-preview") ? json(preview)
    : path.endsWith("/override") ? json({ detail: "PRIVATE_SYNTHETIC_DIAGNOSTIC" }, 409) : json({ current: rule() })));
  const { onChanged } = mount(); open();
  fireEvent.click(await screen.findByRole("button", { name: "Review ZIP policy change" }));
  await screen.findByRole("region", { name: "ZIP policy change preview" }); confirmReason();
  fireEvent.click(screen.getByRole("button", { name: "Confirm ZIP policy change" }));
  expect(await screen.findByRole("alert")).not.toHaveTextContent("PRIVATE_SYNTHETIC_DIAGNOSTIC");
  expect(screen.queryByRole("region", { name: "ZIP policy change preview" })).not.toBeInTheDocument();
  expect(onChanged).not.toHaveBeenCalled();
});

it.each(["LEADERSHIP", "PRODUCTION_LEAD", "PROCESSOR"] as const)("does not offer saved-policy overrides to %s", async (role) => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ current: rule() }))); mount(role); open();
  await screen.findByText(policyLabel(policyValues[1]), { exact: true });
  expect(screen.queryByRole("button", { name: "Review ZIP policy change" })).not.toBeInTheDocument();
});

it.each(["PRODUCTION_LEAD", "PROCESSOR"] as const)("does not offer first selection to %s", async (role) => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => json(path.endsWith("/inventory") ? observed : path.endsWith("/technical-review") ? technical : { current: null })));
  mount(role); open(); await screen.findByText("No ZIP policy has been saved.");
  expect(screen.queryByRole("button", { name: "Save ZIP policy" })).not.toBeInTheDocument();
});

it("requires Done and a current successful technical report", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => json(path.endsWith("/inventory") ? observed
    : path.endsWith("/technical-review") ? { ...technical, validation: null } : { current: null })));
  mount("ADMIN", false); open();
  await screen.findByText(/run a successful current technical check/);
  expect(screen.queryByRole("button", { name: "Save ZIP policy" })).not.toBeInTheDocument();
});

it("pages immutable decisions without changing the current rule", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => json(path.includes("/history?before=2")
    ? { items: [rule()], next_before: null } : path.endsWith("/history")
      ? { items: [rule(2)], next_before: 2 } : { current: rule(2) })));
  mount(); open();
  await screen.findByText(policyLabel(policyValues[0]), { exact: true });
  fireEvent.click(screen.getByRole("button", { name: "Load policy history" }));
  fireEvent.click(await screen.findByRole("button", { name: "Load older policy decisions" }));
  await screen.findByText(/Version 1:/);
  expect(screen.getByText(/Version 2:/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Load older policy decisions" })).not.toBeInTheDocument();
});

it.each([{ revision: 0 }, { policy: "UNKNOWN" }, { previous_id: id }, { inventory_id: null }, { kind: "OVERRIDE" },
  { evidence_hash: "invalid" }, { storage_timezone: "" }, { reason: "" }])("rejects malformed saved policy %j", (change) => {
  expect(() => policyFromDto({ ...rule(), ...change })).toThrow();
});
it("rejects a response for a different material", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ current: { ...rule(), material_id: nextId } })));
  await expect(packagingPolicyClient.current(materialDto.id)).rejects.toThrow("Wrong policy material");
});
it("rejects changed safety effects in the override preview", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ ...preview, effects: { ...preview.effects, source_files_modified: true } })));
  await expect(packagingPolicyClient.preview(materialDto.id, policyFromDto(rule()), policyValues[0])).rejects.toThrow("Inconsistent policy preview");
});
it("loads errors with a bounded retry and never invents a current rule", async () => {
  const fetch = vi.fn().mockRejectedValueOnce(new Error("Synthetic read failure")).mockResolvedValue(json({ current: rule() }));
  vi.stubGlobal("fetch", fetch); mount(); open();
  await screen.findByText("ZIP policy could not be loaded.");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.getByText(policyLabel(policyValues[1]), { exact: true })).toBeVisible());
});
