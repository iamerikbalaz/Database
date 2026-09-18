import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { NotionCompanyPanel } from "./NotionCompanyPanel";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";
import { setSessionToken } from "../auth/sessionTransport";
import { companies } from "../api/mockData";
import { processorDto } from "../test/materialFixtures";
import { notionCompanyId, notionConfig, notionComparisonDto, notionId } from "../test/notionFixtures";
import type { Company } from "../types";

const company = { ...companies[0], id: notionCompanyId, notionPageId: notionId };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
function mount(role: Role = "ADMIN", initial: Company = company) {
  setSessionToken("s".repeat(43));
  const view = (value: Company) => <SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "s".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}><NotionCompanyPanel company={value} /></SessionContext.Provider>;
  const result = render(view(initial)); return { ...result, company: (value: Company) => result.rerender(view(value)) };
}
function toggle(open = true) {
  const details = screen.getByText("Notion company comparison", { selector: "summary" }).parentElement as HTMLDetailsElement;
  details.open = open; fireEvent(details, new Event("toggle"));
}
function server() {
  const fetch = vi.fn(async (_: string, options?: RequestInit) => json(options?.method === "POST" ? notionComparisonDto() : notionConfig()));
  vi.stubGlobal("fetch", fetch); return fetch;
}
it("loads lazily and performs one explicit read, displaying values without applying them", async () => {
  const fetch = server(); mount(); expect(fetch).not.toHaveBeenCalled(); toggle();
  const button = await screen.findByRole("button", { name: "Compare with Notion" }); expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(button); const results = await screen.findByRole("region", { name: "Company comparison results" });
  expect(within(results).getByText("Synthetic česká company")).toBeVisible();
  expect(within(results).getByText("Empty")).toBeVisible(); expect(within(results).getAllByText("Different")).toHaveLength(2);
  expect(within(results).getByText("Same")).toBeVisible(); expect(within(results).queryByRole("link")).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(2); expect(fetch.mock.calls[1][1]?.method).toBe("POST");
  expect(screen.queryByRole("button", { name: /apply|save|synchronize/i })).not.toBeInTheDocument();
});
it.each(["LEADERSHIP", "PRODUCTION_LEAD", "PROCESSOR"] as const)("does not offer comparison to %s", (role) => {
  const fetch = server(); mount(role); expect(screen.queryByText("Notion company comparison")).not.toBeInTheDocument(); expect(fetch).not.toHaveBeenCalled();
});
it.each([null, "not-a-page-id"])("requires an explicit valid page link: %s", async (link) => {
  const fetch = server(); mount("ADMIN", { ...company, notionPageId: link }); toggle();
  expect(await screen.findByRole("button", { name: "Compare with Notion" })).toBeDisabled();
  expect(screen.getByText(/needs a valid Notion page ID/)).toBeVisible(); expect(fetch).toHaveBeenCalledTimes(1);
});
it("shows disabled deployment without attempting an external read", async () => {
  const fetch = server(); fetch.mockResolvedValue(json({ ...notionConfig(), enabled: false, mapped_fields: [] })); mount(); toggle();
  expect(await screen.findByText("Notion comparison is disabled on the server.")).toBeVisible();
  expect(screen.getByRole("button", { name: "Compare with Notion" })).toBeDisabled(); expect(fetch).toHaveBeenCalledTimes(1);
});
it.each(["NOTION_LOCAL_CHANGED", "NOTION_RATE_LIMITED", "NOTION_BUSY", "NOTION_SCHEMA_CHANGED", "NOTION_DISABLED", "NOTION_UNAVAILABLE"])("handles %s without retaining stale results or automatically retrying", async (code) => {
  const fetch = server(); mount(); toggle(); fireEvent.click(await screen.findByRole("button", { name: "Compare with Notion" }));
  await screen.findByRole("region", { name: "Company comparison results" });
  fetch.mockResolvedValueOnce(json({ detail: { code, private: "SYNTHETIC-PRIVATE-CONTENT" } }, 503));
  fireEvent.click(screen.getByRole("button", { name: "Compare with Notion" }));
  await screen.findByRole("alert"); expect(screen.queryByRole("region", { name: "Company comparison results" })).not.toBeInTheDocument();
  expect(screen.queryByText(/SYNTHETIC-PRIVATE-CONTENT/)).not.toBeInTheDocument(); expect(fetch).toHaveBeenCalledTimes(3);
});
it("does not attach a delayed comparison to another company", async () => {
  const fetch = server(); const view = mount(); toggle(); const button = await screen.findByRole("button", { name: "Compare with Notion" });
  let finish!: (value: Response) => void; fetch.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  fireEvent.click(button); fireEvent.click(button); expect(fetch).toHaveBeenCalledTimes(2);
  const next = { ...company, id: crypto.randomUUID(), notionPageId: crypto.randomUUID() }; view.company(next);
  await screen.findByText(`Linked page: ${next.notionPageId}`);
  await act(async () => { finish(json(notionComparisonDto())); });
  expect(screen.queryByRole("region", { name: "Company comparison results" })).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(3);
});
it("discards a late response after collapsing the panel", async () => {
  const fetch = server(); mount(); toggle(); const button = await screen.findByRole("button", { name: "Compare with Notion" });
  let finish!: (value: Response) => void; fetch.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  fireEvent.click(button); toggle(false); await act(async () => { finish(json(notionComparisonDto())); }); toggle();
  await screen.findByRole("button", { name: "Compare with Notion" });
  expect(screen.queryByRole("region", { name: "Company comparison results" })).not.toBeInTheDocument(); expect(fetch).toHaveBeenCalledTimes(3);
});
it("renders property content as text and rejects an unrelated success", async () => {
  const fetch = server(); mount(); toggle(); await screen.findByRole("button", { name: "Compare with Notion" });
  const dto = notionComparisonDto(); dto.fields[1].observed = '<img src="https://example.invalid/" onerror="alert(1)">';
  fetch.mockResolvedValueOnce(json(dto)); fireEvent.click(screen.getByRole("button", { name: "Compare with Notion" }));
  await screen.findByText(dto.fields[1].observed); expect(screen.queryByRole("img")).not.toBeInTheDocument();
  fetch.mockResolvedValueOnce(json(notionComparisonDto(crypto.randomUUID()))); fireEvent.click(screen.getByRole("button", { name: "Compare with Notion" }));
  await screen.findByRole("alert"); expect(screen.queryByRole("region", { name: "Company comparison results" })).not.toBeInTheDocument();
});
it("retries a failed configuration read explicitly", async () => {
  const fetch = server(); fetch.mockRejectedValueOnce(new TypeError("Synthetic transport failure")); mount(); toggle();
  fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Compare with Notion" })).toBeEnabled()); expect(fetch).toHaveBeenCalledTimes(2);
});
