import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { mockApiClient } from "./api/client";
import { companies } from "./api/mockData";
import type { Company } from "./types";
import { SessionContext } from "./auth/context";
import { processorDto } from "./test/materialFixtures";
import { navigationRecoveryMessage } from "./navigationGuard";

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState({}, "", "/projects");
  window.history.pushState({}, "", "/companies");
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
});
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); window.history.replaceState({}, "", "/"); });
function setup(updateCompany: (id: string, payload: object, key?: string) => Promise<Company>) {
  const getProjects = vi.fn(mockApiClient.getProjects), getCompany = vi.fn(mockApiClient.getCompany);
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <App client={{ ...mockApiClient, updateCompany, getProjects, getCompany }} />
  </SessionContext.Provider>);
  return { getProjects, getCompany };
}
async function browserBack() {
  await act(async () => {
    const moved = new Promise<void>(resolve => window.addEventListener("popstate", () => resolve(), { once: true }));
    window.history.back();
    await moved;
  });
}
it("retains an in-flight cell request across sidebar, detail and actual browser Back navigation", async () => {
  let finish!: (value: Company) => void;
  const update = vi.fn().mockReturnValue(new Promise<Company>(resolve => { finish = resolve; }));
  const { getProjects, getCompany } = setup(update);
  fireEvent.change(await screen.findByRole("combobox", { name: `Active for ${companies[0].name}` }), { target: { value: "false" } });
  fireEvent.click(screen.getByRole("link", { name: "Projects" }));
  expect(screen.getByRole("alert")).toHaveTextContent(navigationRecoveryMessage);
  fireEvent.click(screen.getByRole("link", { name: companies[0].name }));
  expect(screen.getByRole("heading", { name: "Companies" })).toBeVisible();
  expect(getProjects).not.toHaveBeenCalled(); expect(getCompany).not.toHaveBeenCalled();
  await browserBack();
  expect(window.location.pathname).toBe("/companies");
  expect(screen.getByRole("heading", { name: "Companies" })).toBeVisible();
  expect(update).toHaveBeenCalledTimes(1);
  expect(window.dispatchEvent(new Event("beforeunload", { cancelable: true }))).toBe(false);
  await act(async () => finish({ ...companies[0], status: "inactive", updatedAt: "2026-09-26T12:00:00Z" }));
  await screen.findByText("Saved. Refresh to reapply filters.");
  expect(window.dispatchEvent(new Event("beforeunload", { cancelable: true }))).toBe(true);
  fireEvent.click(screen.getByRole("link", { name: "Projects" }));
  await screen.findByRole("link", { name: "Swisspearl facade collection" });
  expect(window.location.pathname).toBe("/projects"); expect(screen.queryByText(navigationRecoveryMessage)).not.toBeInTheDocument();
});
it("keeps unknown writes mounted until the identical request is recovered", async () => {
  const update = vi.fn().mockRejectedValueOnce(new TypeError("Lost reply"))
    .mockResolvedValueOnce({ ...companies[0], status: "inactive", updatedAt: "2026-09-26T12:00:00Z" });
  setup(update);
  fireEvent.change(await screen.findByRole("combobox", { name: `Active for ${companies[0].name}` }), { target: { value: "false" } });
  await screen.findByRole("button", { name: "Retry same request" });
  fireEvent.click(screen.getByRole("link", { name: "Add company" }));
  expect(window.location.pathname).toBe("/companies"); expect(screen.getByRole("heading", { name: "Companies" })).toBeVisible();
  await browserBack(); expect(window.location.pathname).toBe("/companies");
  fireEvent.click(screen.getByRole("button", { name: "Retry same request" }));
  await screen.findByText("Saved. Refresh to reapply filters.");
  expect(update.mock.calls[1]).toEqual(update.mock.calls[0]);
  fireEvent.click(screen.getByRole("link", { name: "Add company" }));
  await waitFor(() => expect(window.location.pathname).toBe("/companies/new"));
});
it("allows ordinary sidebar and browser navigation when there is no pending write", async () => {
  const update = vi.fn(); const { getProjects } = setup(update);
  await screen.findByRole("link", { name: companies[0].name });
  await browserBack();
  expect(window.location.pathname).toBe("/projects");
  await screen.findByRole("link", { name: "Swisspearl facade collection" });
  expect(getProjects).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("link", { name: "Companies" }));
  await screen.findByRole("link", { name: companies[0].name });
  expect(window.location.pathname).toBe("/companies"); expect(update).not.toHaveBeenCalled();
});
