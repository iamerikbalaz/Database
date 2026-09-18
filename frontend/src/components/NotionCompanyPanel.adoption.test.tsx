import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NotionCompanyPanel } from "./NotionCompanyPanel";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import { companies } from "../api/mockData";
import { processorDto } from "../test/materialFixtures";
import { notionCompanyId, notionConfig, notionComparisonDto, notionId } from "../test/notionFixtures";
import { notionAdoptionDto } from "../test/notionAdoptionFixtures";
import type { NotionAdoptionRequest } from "../api/notionAdoptionClient";
import type { Company } from "../types";

const company = { ...companies[0], id: notionCompanyId, notionPageId: notionId };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let actorId: string;
beforeEach(() => { actorId = crypto.randomUUID(); });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
function mount() {
  setSessionToken("s".repeat(43)); const applied = vi.fn(), navigate = vi.fn();
  const view = (value: Company, actor = actorId) => <SessionContext.Provider value={{ session: { user: { ...processorDto, id: actor, role: "ADMIN" }, must_change_password: false, csrf_token: "s".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}><NotionCompanyPanel company={value} onApplied={applied} navigate={navigate} /></SessionContext.Provider>;
  const result = render(view(company));
  return { ...result, applied, navigate, company: (value: Company) => result.rerender(view(value)), actor: (actor: string) => result.rerender(view(company, actor)) };
}
function toggle(open = true) {
  const details = screen.getByText("Notion company comparison", { selector: "summary" }).parentElement as HTMLDetailsElement;
  details.open = open; fireEvent(details, new Event("toggle"));
}
function server() {
  const commands: NotionAdoptionRequest[] = [];
  const adopt = vi.fn(async (body: NotionAdoptionRequest) => json(notionAdoptionDto(body, company.id, actorId)));
  const recover = vi.fn(async () => json(notionAdoptionDto(commands[0], company.id, actorId)));
  const fetch = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/notion-preview")) return json(notionComparisonDto());
    if (url.endsWith("/notion-adopt")) { const body = JSON.parse(String(options?.body)) as NotionAdoptionRequest; commands.push(body); return adopt(body); }
    if (url.includes("/notion-adoptions/")) return recover();
    return json(notionConfig());
  });
  vi.stubGlobal("fetch", fetch); return { fetch, adopt, recover, commands };
}
async function review() {
  toggle(); fireEvent.click(await screen.findByRole("button", { name: "Compare with Notion" }));
  await screen.findByRole("region", { name: "Company comparison results" });
  fireEvent.click(screen.getByLabelText("Adopt Name"));
  fireEvent.click(screen.getByLabelText("Adopt Country (clear local value)"));
  fireEvent.change(screen.getByLabelText("Reason for company change"), { target: { value: "Reviewed synthetic fields" } });
  fireEvent.click(screen.getByLabelText("I reviewed the selected local replacements."));
}
it("requires explicit fields, reason and confirmation, then reports the immutable saved change", async () => {
  const api = server(), view = mount(); toggle(); fireEvent.click(await screen.findByRole("button", { name: "Compare with Notion" }));
  const save = await screen.findByRole("button", { name: "Adopt selected values" }); expect(save).toBeDisabled();
  expect(screen.queryByLabelText("Adopt Website")).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Adopt Name")); expect(save).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Reason for company change"), { target: { value: "Reviewed synthetic fields" } }); expect(save).toBeDisabled();
  fireEvent.click(screen.getByLabelText("I reviewed the selected local replacements.")); expect(save).toBeEnabled();
  fireEvent.click(screen.getByLabelText("Adopt Country (clear local value)")); expect(save).toBeDisabled();
  fireEvent.click(screen.getByLabelText("I reviewed the selected local replacements.")); fireEvent.click(save); fireEvent.click(save);
  expect(await screen.findByText(/Saved company change 1/)).toBeVisible(); expect(api.adopt).toHaveBeenCalledTimes(1); expect(view.applied).toHaveBeenCalledTimes(1);
  expect(api.commands[0]).toMatchObject({ selected_fields: ["country", "name"], reason: "Reviewed synthetic fields", expected_page_id: notionId, expected_local_sha256: "a".repeat(64), expected_observation_sha256: "c".repeat(64) });
  expect(Object.keys(api.commands[0])).toHaveLength(6); expect(screen.queryByRole("region", { name: "Pending Notion adoption" })).not.toBeInTheDocument();
});
it("keeps one exact uncertain request through collapsing and reopening without automatic retry", async () => {
  const api = server(); api.adopt.mockRejectedValueOnce(new TypeError("Synthetic lost response")); mount(); await review();
  fireEvent.click(screen.getByRole("button", { name: "Adopt selected values" })); await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "Compare with Notion" })).toBeDisabled(); const first = api.commands[0];
  toggle(false); toggle(); await screen.findByRole("button", { name: "Retry same adoption" });
  expect(api.commands).toHaveLength(1); expect(screen.getByText(`Request: ${first.request_key}`)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Retry same adoption" })); await screen.findByText(/Saved company change 1/);
  expect(api.commands).toEqual([first, first]);
});
it("retains a pending command across company navigation and sends it only for its original company", async () => {
  const api = server(); api.adopt.mockRejectedValueOnce(new TypeError("Synthetic lost response")); const view = mount(); await review();
  fireEvent.click(screen.getByRole("button", { name: "Adopt selected values" })); await screen.findByRole("alert");
  view.company({ ...company, id: crypto.randomUUID() });
  expect(await screen.findByText(/This request belongs to another company/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Retry same adoption" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Return to company with pending adoption" })); expect(view.navigate).toHaveBeenCalledWith(`/companies/${company.id}`);
  view.company({ ...company, updatedAt: "2026-09-18T20:00:00Z", notionPageId: null });
  fireEvent.click(await screen.findByRole("button", { name: "Retry same adoption" })); await screen.findByText(/Saved company change 1/);
  expect(api.commands).toHaveLength(2); expect(api.commands[1]).toEqual(api.commands[0]);
});
it("reads recovery explicitly, keeps an absent result uncertain, and accepts the saved result without resending", async () => {
  const api = server(); api.adopt.mockRejectedValueOnce(new TypeError("Synthetic lost response")); api.recover.mockResolvedValueOnce(json({ detail: { code: "NOTION_ADOPTION_NOT_FOUND" } }, 404));
  const view = mount(); await review(); fireEvent.click(screen.getByRole("button", { name: "Adopt selected values" })); await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Check saved adoption result" })); await screen.findByText(/No saved result is visible yet/);
  expect(screen.getByRole("button", { name: "Compare with Notion" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Check saved adoption result" })); await screen.findByText(/Saved company change 1/);
  expect(api.adopt).toHaveBeenCalledTimes(1); expect(api.recover).toHaveBeenCalledTimes(2); expect(view.applied).toHaveBeenCalledTimes(1);
});
it("rejects an initial stale command and requires a new comparison", async () => {
  const api = server(); api.adopt.mockResolvedValueOnce(json({ detail: { code: "NOTION_LOCAL_CHANGED", raw: "SYNTHETIC-PRIVATE-CONTENT" } }, 409)); mount(); await review();
  fireEvent.click(screen.getByRole("button", { name: "Adopt selected values" })); await screen.findByText(/The adoption was rejected/);
  expect(screen.queryByRole("region", { name: "Company comparison results" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Compare with Notion" })).toBeEnabled(); expect(screen.queryByText(/SYNTHETIC-PRIVATE-CONTENT/)).not.toBeInTheDocument();
});
it("keeps an unknown command after a later access denial", async () => {
  const api = server(); api.adopt.mockRejectedValueOnce(new TypeError("Synthetic lost response")).mockResolvedValueOnce(json({ detail: "Forbidden" }, 403)); mount(); await review();
  fireEvent.click(screen.getByRole("button", { name: "Adopt selected values" })); await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Retry same adoption" }));
  await waitFor(() => expect(api.commands).toHaveLength(2)); await waitFor(() => expect(screen.getByRole("button", { name: "Retry same adoption" })).toBeEnabled());
  expect(screen.getByRole("button", { name: "Compare with Notion" })).toBeDisabled(); expect(api.commands[1]).toEqual(api.commands[0]);
});
it("does not expose or send another actor's pending packet", async () => {
  const api = server(); api.adopt.mockRejectedValueOnce(new TypeError("Synthetic lost response")); const view = mount(); await review();
  fireEvent.click(screen.getByRole("button", { name: "Adopt selected values" })); await screen.findByRole("alert");
  view.actor(crypto.randomUUID()); await waitFor(() => expect(screen.getByRole("button", { name: "Compare with Notion" })).toBeEnabled());
  expect(screen.queryByText(`Request: ${api.commands[0].request_key}`)).not.toBeInTheDocument(); expect(api.commands).toHaveLength(1);
});
it("does not attach a delayed success to a different company", async () => {
  const api = server(); let finish!: (value: Response) => void; api.adopt.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const view = mount(); await review(); fireEvent.click(screen.getByRole("button", { name: "Adopt selected values" }));
  await waitFor(() => expect(api.commands).toHaveLength(1)); view.company({ ...company, id: crypto.randomUUID() });
  await act(async () => finish(json(notionAdoptionDto(api.commands[0], company.id, actorId))));
  expect(view.applied).not.toHaveBeenCalled(); expect(screen.queryByText(/Saved company change 1/)).not.toBeInTheDocument();
});
it("can recover a saved result even after the integration is disabled", async () => {
  const api = server(); api.adopt.mockRejectedValueOnce(new TypeError("Synthetic lost response")); mount(); await review();
  fireEvent.click(screen.getByRole("button", { name: "Adopt selected values" })); await screen.findByRole("alert");
  toggle(false); api.fetch.mockResolvedValueOnce(json({ ...notionConfig(), enabled: false, mapped_fields: [] })); toggle();
  await screen.findByText("Notion comparison is disabled on the server.");
  fireEvent.click(screen.getByRole("button", { name: "Check saved adoption result" })); await screen.findByText(/Saved company change 1/);
  expect(api.commands).toHaveLength(1); expect(api.recover).toHaveBeenCalledTimes(1);
});
