import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ResourceHistoryPanel } from "./ResourceHistoryPanel";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";
import { processorDto } from "../test/materialFixtures";
import { historyResourceId, resourceHistoryDto } from "../test/resourceHistoryFixtures";
import { resourceHistoryNames, type ResourceKind } from "../api/resourceHistoryClient";

const json = (body: unknown) => new Response(JSON.stringify(body));
afterEach(() => vi.unstubAllGlobals());
function mount(kind: ResourceKind = "PROJECT", role: Role = "ADMIN") {
  const view = (id: string, actor = processorDto.id, currentRole = role) => <SessionContext.Provider value={{ session: { user: { ...processorDto, id: actor, role: currentRole }, must_change_password: false, csrf_token: "s".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}><ResourceHistoryPanel kind={kind} id={id} updatedAt="2026-09-18T18:00:00Z" /></SessionContext.Provider>;
  const result = render(view(historyResourceId)); return { ...result, change: (id: string, actor?: string, currentRole?: Role) => result.rerender(view(id, actor, currentRole)) };
}
function open(kind: ResourceKind = "PROJECT") {
  const details = screen.getByText(`${resourceHistoryNames[kind]} change history`, { selector: "summary" }).parentElement as HTMLDetailsElement;
  details.open = true; fireEvent(details, new Event("toggle"));
}
function server(kind: ResourceKind = "PROJECT") { const fetch = vi.fn().mockResolvedValue(json(resourceHistoryDto(kind))); vi.stubGlobal("fetch", fetch); return fetch; }
it.each(["BRAND", "PROJECT", "USER", "MATERIAL"] as const)("lazily displays %s historical values and coverage", async (kind) => {
  const fetch = server(kind); mount(kind); expect(fetch).not.toHaveBeenCalled(); open(kind);
  fireEvent.click(await screen.findByText(/^Change 2 · Record updated/)); expect(screen.getByText("Previous synthetic name")).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(1); expect(screen.queryByRole("button", { name: /save|delete|restore/i })).not.toBeInTheDocument();
  if (kind === "USER") expect(screen.getByText(/does not include sign-ins/)).toBeVisible();
  if (kind === "MATERIAL") expect(screen.getByText(/have separate histories/)).toBeVisible();
});
it.each(["PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP"] as const)("does not load administrator history for %s", (role) => {
  const fetch = server(); mount("PROJECT", role); expect(screen.queryByText("Project change history")).not.toBeInTheDocument(); expect(fetch).not.toHaveBeenCalled();
});
it("pages on request and returns to latest changes", async () => {
  const fetch = server(), first = resourceHistoryDto("PROJECT", 21); first.items = first.items.slice(0, 20); first.next_cursor = first.items[19].id;
  fetch.mockResolvedValueOnce(json(first)).mockResolvedValueOnce(json(resourceHistoryDto("PROJECT", 1))).mockResolvedValueOnce(json(first));
  mount(); open(); await screen.findByText(/^Change 21 ·/); fireEvent.click(screen.getByRole("button", { name: "Older changes" }));
  await screen.findByText(/^Change 1 ·/); expect(fetch.mock.calls[1][0]).toContain(`?after=${first.next_cursor}`);
  expect(screen.getByRole("button", { name: "Older changes" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Latest changes" })); await screen.findByText(/^Change 21 ·/);
});
it("retries a failed read and displays empty history", async () => {
  server().mockRejectedValueOnce(new TypeError("Synthetic offline")).mockResolvedValueOnce(json(resourceHistoryDto("PROJECT", 0)));
  mount(); open(); fireEvent.click(await screen.findByRole("button", { name: "Try again" })); expect(await screen.findByText("No recorded profile changes.")).toBeVisible();
});
it.each(["resource", "actor", "role"])("discards a late response after %s changes", async (change) => {
  const fetch = server(); let finish!: (response: Response) => void;
  fetch.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const view = mount(); open(); await act(async () => {});
  const other = change === "resource" ? crypto.randomUUID() : historyResourceId;
  fetch.mockResolvedValueOnce(json(resourceHistoryDto("PROJECT", 0, other)));
  view.change(other, change === "actor" ? crypto.randomUUID() : undefined, change === "role" ? "PROCESSOR" : "ADMIN");
  if (change !== "role") await screen.findByText("No recorded profile changes.");
  await act(async () => finish(json(resourceHistoryDto()))); expect(screen.queryByText(/^Change 2 ·/)).not.toBeInTheDocument();
});
