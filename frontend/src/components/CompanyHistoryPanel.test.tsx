import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CompanyHistoryPanel } from "./CompanyHistoryPanel";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";
import { companies } from "../api/mockData";
import { processorDto } from "../test/materialFixtures";
import { notionCompanyId } from "../test/notionFixtures";
import { companyHistoryDto } from "../test/companyHistoryFixtures";

const company = { ...companies[0], id: notionCompanyId };
const json = (body: unknown) => new Response(JSON.stringify(body));
afterEach(() => vi.unstubAllGlobals());
function mount(role: Role = "ADMIN") {
  const view = (id: string) => <SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "s".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}><CompanyHistoryPanel company={{ ...company, id }} /></SessionContext.Provider>;
  const result = render(view(company.id)); return { ...result, company: (id: string) => result.rerender(view(id)) };
}
function open() {
  const details = screen.getByText("Company change history", { selector: "summary" }).parentElement as HTMLDetailsElement;
  details.open = true; fireEvent(details, new Event("toggle"));
}
function server() { const fetch = vi.fn().mockResolvedValue(json(companyHistoryDto())); vi.stubGlobal("fetch", fetch); return fetch; }
it("loads lazily and renders historical changes with clear before and after values", async () => {
  const fetch = server(); mount(); expect(fetch).not.toHaveBeenCalled(); open();
  const summary = await screen.findByText(/^Change 2 · Company updated/); fireEvent.click(summary);
  expect(screen.getByText("Previous synthetic company")).toBeVisible();
  expect(screen.getAllByText("Synthetic company")[0]).toBeVisible(); expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: /save|restore|delete/i })).not.toBeInTheDocument();
});
it.each(["PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP"] as const)("hides administrator history from %s", (role) => {
  const fetch = server(); mount(role); expect(screen.queryByText("Company change history")).not.toBeInTheDocument(); expect(fetch).not.toHaveBeenCalled();
});
it("pages only on request and returns to latest history", async () => {
  const fetch = server(); const first = companyHistoryDto(21); first.items = first.items.slice(0, 20); first.next_cursor = first.items[19].id;
  fetch.mockResolvedValueOnce(json(first)).mockResolvedValueOnce(json(companyHistoryDto(1))).mockResolvedValueOnce(json(first));
  mount(); open(); await screen.findByText(/^Change 21 ·/); expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Older changes" })); await screen.findByText(/^Change 1 ·/);
  expect(screen.getByRole("button", { name: "Older changes" })).toBeDisabled(); expect(fetch.mock.calls[1][0]).toContain(`?after=${first.next_cursor}`);
  fireEvent.click(screen.getByRole("button", { name: "Latest changes" })); await screen.findByText(/^Change 21 ·/); expect(fetch).toHaveBeenCalledTimes(3);
});
it("shows an empty history and retries a failed read", async () => {
  const fetch = server(); fetch.mockRejectedValueOnce(new TypeError("Synthetic offline")).mockResolvedValueOnce(json(companyHistoryDto(0)));
  mount(); open(); fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
  expect(await screen.findByText("No recorded company changes.")).toBeVisible(); expect(fetch).toHaveBeenCalledTimes(2);
});
it("never renders a previous company's delayed history", async () => {
  const fetch = server(); let finish!: (response: Response) => void;
  fetch.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const view = mount(); open(); await act(async () => {}); const other = crypto.randomUUID();
  fetch.mockResolvedValueOnce(json(companyHistoryDto(0, other))); view.company(other);
  await screen.findByText("No recorded company changes."); await act(async () => finish(json(companyHistoryDto())));
  expect(screen.queryByText(/^Change 2 ·/)).not.toBeInTheDocument();
});
