import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MaterialAiPanel } from "./MaterialAiPanel";
import { aiContentClient, type AiDraft } from "../api/aiContentClient";
import { catalogClient } from "../api/catalogClient";
import { ApiError } from "../api/errors";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";
import { materialDto, processorDto } from "../test/materialFixtures";

const id = materialDto.id, sourceId = "10000000-0000-4000-8000-000000000001", draftId = "20000000-0000-4000-8000-000000000001";
const context = { materialId: id, name: "Synthetic stone", brand: { id: materialDto.published_brand_id, name: "Synthetic brand" }, categories: [], collections: [], sourceUrls: [{ id: sourceId, url: "https://catalog.example/stone" }] };
const draft: AiDraft = { id: draftId, materialId: id, actorId: processorDto.id, context, contextHash: "a".repeat(64), contentRevision: 2, provider: "Synthetic provider", model: "fixture-v1", promptVersion: "pbr-1", description: "Proposed stone description", tags: ["stone"], sourceIds: [sourceId], reason: "Prepared proposal", createdAt: "2026-09-17T12:00:00Z", contextIsCurrent: true };
const content = { materialId: id, revision: 2, description: "Current description", tags: ["matte"], credits: 8, categories: [], collections: [], status: "MANUAL_DRAFT" as const };
afterEach(() => vi.restoreAllMocks());
function setup(role: Role = "ADMIN", current = true) {
  const ctx = vi.spyOn(aiContentClient, "context").mockResolvedValue({ context, contextHash: "a".repeat(64), contentRevision: 2 });
  vi.spyOn(catalogClient, "content").mockResolvedValue(content);
  vi.spyOn(aiContentClient, "sources").mockResolvedValue([{ id: sourceId, url: context.sourceUrls[0].url, active: true, version: 1 }]);
  const history = vi.spyOn(aiContentClient, "drafts").mockResolvedValue({ items: [{ ...draft, contextIsCurrent: current }], nextCursor: null });
  const submit = vi.spyOn(aiContentClient, "submit").mockResolvedValue(draft);
  const adopt = vi.spyOn(aiContentClient, "adopt").mockResolvedValue({ ...content, revision: 3, status: "AI_DRAFT" });
  const approval = vi.spyOn(aiContentClient, "approveSource").mockResolvedValue({ id: sourceId, url: context.sourceUrls[0].url, active: true, version: 1 });
  const activity = vi.spyOn(aiContentClient, "sourceActivity").mockResolvedValue({ id: sourceId, url: context.sourceUrls[0].url, active: false, version: 2 });
  const changed = vi.fn(), sourcesChanged = vi.fn();
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialAiPanel materialId={id} onAdopted={changed} onSourcesChanged={sourcesChanged} />
  </SessionContext.Provider>);
  return { ctx, history, submit, adopt, approval, activity, changed, sourcesChanged };
}
async function load() { fireEvent.click(screen.getByRole("button", { name: "Load AI workspace" })); await screen.findByRole("heading", { name: "AI proposal history" }); }
function enter(label: string, value: string) { fireEvent.change(screen.getByLabelText(label, { exact: true }), { target: { value } }); }
async function prepareAdoption() {
  await load(); fireEvent.click(screen.getByRole("button", { name: "Compare and review proposal" }));
  enter("Reviewed description", "Human edited description"); enter("Reviewed tags, one per line", "stone\nrough"); enter("Reason for adopting proposal", "Verified text against approved source");
  fireEvent.click(screen.getByRole("checkbox", { name: /I reviewed the text/ }));
}
function prepareProposal() {
  fireEvent.click(screen.getByText("Record an AI proposal", { exact: true }));
  enter("AI provider", "Synthetic provider"); enter("AI model", "fixture-v1"); enter("Prompt version", "pbr-1");
  enter("Proposed description", "Proposed stone description"); enter("Proposed tags, one per line", "stone"); enter("Reason for recording proposal", "Prepared proposal");
  fireEvent.click(screen.getByRole("checkbox", { name: context.sourceUrls[0].url }));
}
it("loads only on request and displays the current content beside an editable proposal", async () => {
  const { ctx, adopt, changed } = setup(); expect(ctx).not.toHaveBeenCalled(); await prepareAdoption();
  expect(within(screen.getByRole("region", { name: "Currently saved content" })).getByText("Current description")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Adopt reviewed content" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(adopt).toHaveBeenCalledWith(id, draftId, { idempotency_key: expect.any(String), expected_revision: 2, expected_context_hash: "a".repeat(64), description: "Human edited description", tags: ["stone", "rough"], reason: "Verified text against approved source" });
});
it("requires explicit review and resets that confirmation when wording changes", async () => {
  setup(); await prepareAdoption(); expect(screen.getByRole("button", { name: "Adopt reviewed content" })).toBeEnabled();
  enter("Reviewed description", "Another edit"); expect(screen.getByRole("checkbox", { name: /I reviewed the text/ })).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Adopt reviewed content" })).toBeDisabled();
});
it("records source-linked proposal history without adopting content", async () => {
  const { submit, adopt, changed } = setup(); await load(); prepareProposal(); fireEvent.click(screen.getByRole("button", { name: "Save AI proposal" }));
  await waitFor(() => expect(submit).toHaveBeenCalledOnce());
  expect(submit).toHaveBeenCalledWith(id, expect.objectContaining({ expected_context_hash: draft.contextHash, source_link_ids: [sourceId], model: "fixture-v1", tags: ["stone"] }));
  expect(adopt).not.toHaveBeenCalled(); expect(changed).not.toHaveBeenCalled();
});
it("freezes all actions on an unknown adoption outcome and retries the exact payload", async () => {
  const { adopt, changed, approval } = setup(); adopt.mockRejectedValueOnce(new TypeError("Synthetic disconnect")); await prepareAdoption();
  fireEvent.click(screen.getByRole("button", { name: "Adopt reviewed content" })); await screen.findByText(/The outcome is unknown/);
  expect(screen.getByLabelText("Reviewed description")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reload AI workspace and discard its unsaved edits" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry same AI request" })); await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(adopt.mock.calls[0]).toEqual(adopt.mock.calls[1]); expect(approval).not.toHaveBeenCalled();
});
it("keeps rejected adoption available for correction or explicit reload", async () => {
  const { adopt, changed } = setup(); adopt.mockRejectedValueOnce(new ApiError(409, "Conflict")); await prepareAdoption();
  fireEvent.click(screen.getByRole("button", { name: "Adopt reviewed content" })); expect(await screen.findByRole("alert")).toHaveTextContent("reload");
  expect(screen.getByLabelText("Reviewed description")).toHaveValue("Human edited description"); expect(screen.getByLabelText("Reviewed description")).toBeEnabled(); expect(changed).not.toHaveBeenCalled();
});
it("shows stale proposals but prevents adoption", async () => {
  setup("ADMIN", false); await load(); expect(screen.getByRole("button", { name: "Compare and review proposal" })).toBeDisabled();
  expect(screen.getByText(/Context has changed/)).toBeVisible();
});
it("fails loading when context and saved revision disagree", async () => {
  const { ctx } = setup(); ctx.mockResolvedValue({ context, contextHash: draft.contextHash, contentRevision: 3 });
  fireEvent.click(screen.getByRole("button", { name: "Load AI workspace" })); expect(await screen.findByRole("alert")).toHaveTextContent("could not be loaded");
  expect(screen.queryByRole("button", { name: "Compare and review proposal" })).not.toBeInTheDocument();
});
it.each(["LEADERSHIP", "PROCESSOR", "PRODUCTION_LEAD"] as Role[])("restricts source approval for %s", async (role) => {
  setup(role); await load(); expect(screen.queryByLabelText("Source URL")).not.toBeInTheDocument();
  expect(screen.queryByText("Record an AI proposal", { exact: true }) !== null).toBe(role !== "LEADERSHIP");
  expect(screen.queryByRole("button", { name: "Compare and review proposal" }) !== null).toBe(role !== "LEADERSHIP");
});
it("uses exact source versions and a reason for availability changes", async () => {
  const { activity, sourcesChanged } = setup(); await load(); fireEvent.click(screen.getByText("Approved source URLs", { exact: true }));
  const button = screen.getByRole("button", { name: `Deactivate ${context.sourceUrls[0].url}` }); expect(button).toBeDisabled();
  enter("Reason for source approval or availability change", "Retire outdated source"); fireEvent.click(button);
  await waitFor(() => expect(sourcesChanged).toHaveBeenCalledOnce());
  expect(activity).toHaveBeenCalledWith(id, sourceId, { idempotency_key: expect.any(String), expected_version: 1, is_active: false, reason: "Retire outdated source" });
});
it("rejects joined tags before sending a proposal", async () => {
  const { submit } = setup(); await load(); prepareProposal(); enter("Proposed tags, one per line", "stone:rough");
  fireEvent.click(screen.getByRole("button", { name: "Save AI proposal" })); expect(await screen.findByRole("alert")).toHaveTextContent("without colons"); expect(submit).not.toHaveBeenCalled();
});
it("loads bounded older history and clears the previous selection", async () => {
  const { history } = setup(); history.mockResolvedValueOnce({ items: [draft], nextCursor: draftId }); await prepareAdoption();
  history.mockResolvedValueOnce({ items: [], nextCursor: null }); fireEvent.click(screen.getByRole("button", { name: "Older proposals" }));
  await screen.findByText("No AI proposals recorded."); expect(history).toHaveBeenLastCalledWith(id, draftId);
  expect(screen.queryByRole("region", { name: "Review AI proposal" })).not.toBeInTheDocument();
});
