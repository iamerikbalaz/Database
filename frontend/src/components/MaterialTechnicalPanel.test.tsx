import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MaterialTechnicalPanel } from "./MaterialTechnicalPanel";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import type { Role } from "../auth/client";
import { materialFromDto } from "../api/materialDto";
import { materialDto, processorDto } from "../test/materialFixtures";

const id = "10000000-0000-4000-8000-000000000001";
const review = { generation: 1, revision_hash: "a".repeat(64), inventory_id: id, checked_at: "2026-09-15T12:00:00Z", failure_code: null };
function fixture(warnings = false, errors = false) {
  return { review, validation: { id, actor_id: id, generation: 1, revision_hash: review.revision_hash, report_hash: "b".repeat(64), created_at: review.checked_at,
    report: { schema_version: 1, validator_version: "pbr-images-1", can_approve: !errors, images: [{ path: "1K/map.png", map: "COL", width: 1024, height: 1024, bits: 8, format: "PNG" }],
      errors: errors ? [{ code: "IMAGE_UNREADABLE", path: "1K/map.png" }] : [], warnings: warnings ? [{ code: "SOURCE_METADATA_MISSING", path: "metadata.txt" }] : [] } }, approvals: [] };
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
function mount(role: Role = "ADMIN", done = true) {
  const onChanged = vi.fn(async () => true); setSessionToken("t".repeat(43));
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialTechnicalPanel material={materialFromDto({ ...materialDto, folder_path: "library/LASVIT_9999_G03", workflow_status: done ? "DONE" : "IN_PROGRESS" })} onChanged={onChanged} />
  </SessionContext.Provider>);
  return onChanged;
}

it("requires explicit warning acknowledgment and note and sends the displayed revision", async () => {
  const fetch = vi.fn(async () => json(fixture(true))); vi.stubGlobal("fetch", fetch); const changed = mount();
  const button = await screen.findByRole("button", { name: "Approve technically" });
  expect(button).toBeDisabled(); expect(screen.getByText("Root metadata.txt is missing", { exact: false })).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox")); expect(button).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Approval note"), { target: { value: "Reviewed missing metadata" } });
  fireEvent.click(button); await screen.findByText("Technical approval saved for this revision.");
  const calls = (fetch.mock.calls as unknown as [string, RequestInit][]);
  const mutation = calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(String(mutation[1].body))).toEqual(expect.objectContaining({ kind: "TECHNICAL", expected_generation: 1,
    expected_revision_hash: review.revision_hash, technical_check_id: id, warnings_acknowledged: true, note: "Reviewed missing metadata" }));
  expect(mutation[1].headers).toEqual(expect.objectContaining({ "X-CSRF-Token": "t".repeat(43) }));
  expect(changed).toHaveBeenCalledOnce();
});

it.each(["PROCESSOR", "LEADERSHIP"] as const)("limits controls for %s", async (role) => {
  vi.stubGlobal("fetch", vi.fn(async () => json(fixture()))); mount(role);
  await screen.findByText("Passed", { exact: true });
  expect(screen.queryByRole("button", { name: "Approve technically" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Approve for publication" })).not.toBeInTheDocument();
  expect(Boolean(screen.queryByRole("button", { name: "Run technical checks" }))).toBe(role === "PROCESSOR");
});

it("offers leadership publication approval only after technical approval", async () => {
  const value = { ...fixture(), approvals: [{ id, actor_id: id, kind: "TECHNICAL", generation: 1, revision_hash: review.revision_hash, created_at: review.checked_at, note: null }] };
  vi.stubGlobal("fetch", vi.fn(async () => json(value))); mount("LEADERSHIP");
  expect(await screen.findByRole("button", { name: "Approve for publication" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Approve technically" })).not.toBeInTheDocument();
});

it.each(["errors", "production"])("blocks approval for %s", async (problem) => {
  vi.stubGlobal("fetch", vi.fn(async () => json(fixture(false, problem === "errors")))); mount("ADMIN", problem !== "production");
  await screen.findByRole("button", { name: "Run technical checks" });
  expect(screen.queryByRole("button", { name: "Approve technically" })).not.toBeInTheDocument();
  if (problem === "errors") expect(screen.getByRole("region", { name: "Technical errors" })).toHaveTextContent("Image cannot be decoded");
});

it("retains the exact request key after an unknown transport result", async () => {
  const bodies: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") { bodies.push(String(init.body)); if (bodies.length === 1) throw new TypeError("Unavailable"); }
    return json(fixture());
  })); mount();
  fireEvent.click(await screen.findByRole("button", { name: "Approve technically" })); await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Approve technically" })); await screen.findByText("Technical approval saved for this revision.");
  expect(bodies).toHaveLength(2); expect(bodies[0]).toBe(bodies[1]);
});

it("refreshes after a rejected approval and never reflects a raw server error", async () => {
  let rejected = false;
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") { rejected = true; return json({ detail: "SYNTHETIC_PRIVATE_DATA" }, 409); }
    return json(rejected ? fixture(false, true) : fixture());
  })); const changed = mount();
  fireEvent.click(await screen.findByRole("button", { name: "Approve technically" }));
  await screen.findByText("Corrections required");
  expect(screen.getByRole("alert")).not.toHaveTextContent("SYNTHETIC_PRIVATE_DATA");
  expect(screen.queryByRole("button", { name: "Approve technically" })).not.toBeInTheDocument();
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
});
