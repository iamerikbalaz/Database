import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ContentApprovalPanel } from "./ContentApprovalPanel";
import { contentReviewFromDto } from "../api/contentReviewClient";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import type { Role } from "../auth/client";
import { materialDto, processorDto } from "../test/materialFixtures";

const id = "10000000-0000-4000-8000-000000000001";
function fixture(warnings = false, errors = false, approved = false) {
  return { material_id: materialDto.id, content_revision: 2, context_hash: "a".repeat(64), can_approve: !errors && !approved,
    content_status: approved ? "APPROVED" : "MANUAL_DRAFT", errors: errors ? ["CONTENT_CREDITS_REQUIRED"] : [], warnings: warnings ? ["CONTENT_DESCRIPTION_EMPTY"] : [],
    snapshot: { schema_version: 1, material: { material_name: "Saved synthetic name", technical_identity: materialDto.technical_identity },
      brand: { name: "Saved brand", brand_identifier: "saved-brand" }, source_review: { generation: 4, revision_hash: null },
      content: { material_id: materialDto.id, revision: 2, description: warnings ? null : "Saved description", credits: errors ? null : 12,
        tags: ["stone"], categories: [{ id, value: "Stone", version: 1, is_active: true }], collections: [], content_status: "MANUAL_DRAFT" } },
    approval: approved ? { id, actor_id: id, content_revision: 2, context_hash: "a".repeat(64), warnings_acknowledged: warnings,
      note: warnings ? "Reviewed" : null, created_at: "2026-09-16T21:00:00Z" } : null };
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
function mount(role: Role = "ADMIN") {
  const changed = vi.fn(); setSessionToken("t".repeat(43));
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <ContentApprovalPanel materialId={materialDto.id} onChanged={changed} />
  </SessionContext.Provider>);
  return changed;
}

it("shows the exact saved content before sending an idempotent leadership decision with CSRF", async () => {
  let approved = false;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => { if (init?.method === "POST") approved = true; return json(fixture(false, false, approved)); });
  vi.stubGlobal("fetch", fetch); const changed = mount("LEADERSHIP");
  expect(screen.queryByRole("button", { name: "Confirm content approval" })).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Review saved content" }));
  const preview = screen.getByRole("region", { name: "Saved content to approve" });
  expect(preview).toHaveTextContent("Saved description"); expect(preview).toHaveTextContent("saved-brand"); expect(preview).toHaveTextContent("12");
  fireEvent.click(within(preview).getByRole("button", { name: "Confirm content approval" }));
  await screen.findByText("Current content is approved.");
  expect(changed).toHaveBeenCalledOnce();
  const [, init] = fetch.mock.calls.find(([, options]) => options?.method === "POST")!;
  expect(JSON.parse(String(init?.body))).toEqual({ idempotency_key: expect.any(String), expected_revision: 2,
    expected_context_hash: "a".repeat(64), warnings_acknowledged: false, note: null });
  expect(init?.headers).toEqual(expect.objectContaining({ "X-CSRF-Token": "t".repeat(43) }));
});
it("requires both warning acknowledgment and a note", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(fixture(true)))); mount();
  fireEvent.click(await screen.findByRole("button", { name: "Review saved content" }));
  const confirm = screen.getByRole("button", { name: "Confirm content approval" });
  expect(confirm).toBeDisabled(); fireEvent.click(screen.getByRole("checkbox")); expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Content approval note"), { target: { value: "Reviewed empty description" } });
  expect(confirm).toBeEnabled(); fireEvent.click(screen.getByRole("checkbox")); expect(confirm).toBeDisabled();
});
it.each(["PROCESSOR", "PRODUCTION_LEAD"] as const)("keeps approval read-only for %s", async (role) => {
  vi.stubGlobal("fetch", vi.fn(async () => json(fixture()))); mount(role);
  await screen.findByText("Current content requires approval.");
  expect(screen.queryByRole("button", { name: "Review saved content" })).not.toBeInTheDocument();
});
it("blocks incomplete drafts", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(fixture(false, true)))); mount();
  expect(await screen.findByRole("list", { name: "Content blockers" })).toHaveTextContent("Enter credits");
  expect(screen.queryByRole("button", { name: "Review saved content" })).not.toBeInTheDocument();
});
it("freezes an unknown outcome and retries the identical decision", async () => {
  const bodies: string[] = []; let approved = false;
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") { bodies.push(String(init.body)); if (bodies.length === 1) throw new TypeError("Synthetic connection loss"); approved = true; }
    return json(fixture(false, false, approved));
  })); const changed = mount();
  fireEvent.click(await screen.findByRole("button", { name: "Review saved content" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm content approval" }));
  await screen.findByRole("alert"); expect(screen.getByLabelText("Content approval note")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reload content approval" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Cancel content approval" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry same content approval" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce()); expect(bodies).toHaveLength(2); expect(bodies[0]).toBe(bodies[1]);
});
it("refreshes a rejected context without reflecting server data", async () => {
  let rejected = false;
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") { rejected = true; return json({ detail: "PRIVATE_SYNTHETIC_MARKER" }, 409); }
    return json(fixture(false, rejected));
  })); const changed = mount();
  fireEvent.click(await screen.findByRole("button", { name: "Review saved content" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm content approval" }));
  expect(await screen.findByRole("alert")).not.toHaveTextContent("PRIVATE_SYNTHETIC_MARKER");
  await screen.findByText("Enter credits."); expect(changed).not.toHaveBeenCalled();
  expect(screen.queryByRole("region", { name: "Saved content to approve" })).not.toBeInTheDocument();
});
it("loads past approval snapshots without treating them as current", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => json(path.endsWith("/content-approvals") ? [{ ...fixture(false, false, true).approval, snapshot: fixture().snapshot }] : fixture()))); mount();
  fireEvent.click(await screen.findByRole("button", { name: "Load content approval history" }));
  expect(await screen.findByText(/Approved revision 2 ·/)).toBeInTheDocument();
  expect(screen.getByText("Current content requires approval.")).toBeVisible();
});
it("refreshes the approval after source work even when the material record is unchanged", async () => {
  const fetch = vi.fn(async () => json(fixture(false, false, true))); vi.stubGlobal("fetch", fetch);
  const changed = vi.fn();
  const { rerender } = render(<ContentApprovalPanel materialId={materialDto.id} refreshVersion={0} onChanged={changed} />);
  await screen.findByText("Current content is approved.");
  fetch.mockResolvedValueOnce(json(fixture()));
  rerender(<ContentApprovalPanel materialId={materialDto.id} refreshVersion={1} onChanged={changed} />);
  await screen.findByText("Current content requires approval.");
  expect(screen.queryByText("Current content is approved.")).not.toBeInTheDocument();
});
it.each([{ context_hash: "invalid" }, { content_revision: 1 }, { can_approve: "yes" }, { content_status: "APPROVED" }, { errors: ["BLOCKED"] }])(
  "rejects inconsistent review data %j", (change) => { expect(() => contentReviewFromDto({ ...fixture(), ...change })).toThrow(); },
);
