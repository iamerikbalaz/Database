import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AccountSecurityPanel } from "./AccountSecurityPanel";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";
import { processorDto } from "../test/materialFixtures";
import { securityEventDto, securityHistoryDto, securityUserId } from "../test/accountSecurityFixtures";
import { internalUserFromDto } from "../api/materialDto";

const json = (body: unknown) => new Response(JSON.stringify(body));
afterEach(() => vi.unstubAllGlobals());
function mount(role: Role = "ADMIN") {
  const view = (id: string, actor = processorDto.id, currentRole = role) => <SessionContext.Provider value={{ session: { user: { ...processorDto, id: actor, role: currentRole }, must_change_password: false, csrf_token: "s".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}><AccountSecurityPanel user={{ ...internalUserFromDto(processorDto), id }} /></SessionContext.Provider>;
  const result = render(view(securityUserId)); return { ...result, change: (id: string, actor?: string, currentRole?: Role) => result.rerender(view(id, actor, currentRole)) };
}
function open() {
  const details = screen.getByText("Account security history", { selector: "summary" }).parentElement as HTMLDetailsElement;
  details.open = true; fireEvent(details, new Event("toggle"));
}
function server() { const fetch = vi.fn().mockResolvedValue(json(securityHistoryDto())); vi.stubGlobal("fetch", fetch); return fetch; }
it("loads only on request and labels historical outcomes without credential values", async () => {
  const fetch = server(); mount(); expect(fetch).not.toHaveBeenCalled(); open();
  const summary = await screen.findByText(/^Security change 2 · Access reset/); fireEvent.click(summary);
  expect(within(summary.parentElement!).getByText(/Password change required after this action/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /reset|restore|delete/i })).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("identifies host recovery without inventing an application actor", async () => {
  const dto = securityHistoryDto(1); dto.items[0] = securityEventDto("HOST_ACCESS_RECOVERED"); server().mockResolvedValueOnce(json(dto));
  mount(); open(); fireEvent.click(await screen.findByText(/^Security change 1 · Access recovered/));
  expect(screen.getByText("Source: local host administration; no signed-in application actor.")).toBeVisible();
});
it.each(["PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP"] as const)("never loads administrator security history for %s", (role) => {
  const fetch = server(); mount(role); expect(screen.queryByText("Account security history")).not.toBeInTheDocument(); expect(fetch).not.toHaveBeenCalled();
});
it("pages explicitly and returns to current history", async () => {
  const fetch = server(), first = securityHistoryDto(21); first.items = first.items.slice(0, 20); first.next_cursor = first.items[19].id;
  fetch.mockResolvedValueOnce(json(first)).mockResolvedValueOnce(json(securityHistoryDto(1))).mockResolvedValueOnce(json(first));
  mount(); open(); await screen.findByText(/^Security change 21 ·/);
  fireEvent.click(screen.getByRole("button", { name: "Older security changes" })); await screen.findByText(/^Security change 1 ·/);
  expect(fetch.mock.calls[1][0]).toContain(`?after=${first.next_cursor}`);
  fireEvent.click(screen.getByRole("button", { name: "Latest security changes" })); await screen.findByText(/^Security change 21 ·/);
});
it("retries a failed read and shows the absence of recorded events", async () => {
  server().mockRejectedValueOnce(new TypeError("Synthetic offline")).mockResolvedValueOnce(json(securityHistoryDto(0)));
  mount(); open(); fireEvent.click(await screen.findByRole("button", { name: "Try again" })); expect(await screen.findByText("No recorded security changes.")).toBeVisible();
});
it.each(["resource", "actor", "role"])("discards delayed security history after %s changes", async (change) => {
  const fetch = server(); let finish!: (response: Response) => void;
  fetch.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; })); const view = mount(); open(); await act(async () => {});
  const other = change === "resource" ? crypto.randomUUID() : securityUserId;
  fetch.mockResolvedValueOnce(json(securityHistoryDto(0, other)));
  view.change(other, change === "actor" ? crypto.randomUUID() : undefined, change === "role" ? "PROCESSOR" : "ADMIN");
  if (change !== "role") await screen.findByText("No recorded security changes.");
  await act(async () => finish(json(securityHistoryDto()))); expect(screen.queryByText(/^Security change 2 ·/)).not.toBeInTheDocument();
});
