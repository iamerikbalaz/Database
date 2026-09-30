import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PackagingSettingsPage, SettingsPage } from "./SettingsPage";
import { packagingSettingsClient as api } from "../api/packagingSettingsClient";
import { ApiError } from "../api/errors";
import { SessionContext } from "../auth/context";
import { processorDto } from "../test/materialFixtures";
import { requestNavigation } from "../navigationGuard";

const initial = { version: 0, cutoffDate: "2026-03-04", storageTimezone: "Europe/Prague" };
afterEach(() => vi.restoreAllMocks());
function mount(role: "ADMIN" | "LEADERSHIP" = "ADMIN") {
  return render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <PackagingSettingsPage navigate={vi.fn()} />
  </SessionContext.Provider>);
}

it("groups imports, packaging and accounts in Settings without loading packaging on the hub", () => {
  const current = vi.spyOn(api, "current");
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}><SettingsPage navigate={vi.fn()} /></SessionContext.Provider>);
  expect(screen.getByRole("link", { name: /Imports/ })).toHaveAttribute("href", "/settings/imports");
  expect(screen.getByRole("link", { name: /Automatic ZIP packaging/ })).toHaveAttribute("href", "/settings/packaging");
  expect(screen.getByRole("link", { name: /Accounts/ })).toHaveAttribute("href", "/settings/users");
  expect(current).not.toHaveBeenCalled();
});

it("shows the global automatic methods and edits with the current version", async () => {
  const current = vi.spyOn(api, "current").mockResolvedValue(initial);
  const save = vi.spyOn(api, "save").mockResolvedValue({ ...initial, version: 1, cutoffDate: "2026-04-01" });
  mount(); await screen.findByLabelText("Cutoff date");
  expect(screen.queryByRole("button", { name: /Refresh settings|Reload settings/ })).not.toBeInTheDocument();
  expect(screen.getByText("Normalize ZIP dates to 1 January 2026.")).toBeVisible();
  fireEvent.change(screen.getByLabelText("Cutoff date"), { target: { value: "2026-04-01" } });
  current.mockResolvedValue({ ...initial, version: 1, cutoffDate: "2026-04-01" });
  fireEvent.click(screen.getByRole("button", { name: "Save packaging settings" }));
  await screen.findByText(/Packaging settings saved/);
  expect(save).toHaveBeenCalledWith({ idempotency_key: expect.any(String), expected_version: 0, cutoff_date: "2026-04-01", storage_timezone: "Europe/Prague" });
});

it("recovers exactly after a lost result and displays newer current settings", async () => {
  const current = vi.spyOn(api, "current").mockResolvedValue(initial);
  const save = vi.spyOn(api, "save").mockRejectedValueOnce(new TypeError("Lost response"))
    .mockResolvedValue({ ...initial, version: 1 });
  mount(); await screen.findByLabelText("Cutoff date");
  fireEvent.click(screen.getByRole("button", { name: "Save packaging settings" }));
  await screen.findByRole("button", { name: "Recover same settings request" });
  expect(requestNavigation("/materials")).toBe(false);
  expect(screen.getByLabelText("Cutoff date")).toBeDisabled();
  current.mockResolvedValue({ ...initial, version: 2, cutoffDate: "2027-01-01" });
  fireEvent.click(screen.getByRole("button", { name: "Recover same settings request" }));
  await waitFor(() => expect(screen.getByLabelText("Cutoff date")).toHaveValue("2027-01-01"));
  expect(save.mock.calls[1]).toEqual(save.mock.calls[0]);
  expect(requestNavigation("/materials")).toBe(true);
});

it("requires refresh for a stale version and restricts editing to admins", async () => {
  vi.spyOn(api, "current").mockResolvedValue(initial);
  vi.spyOn(api, "save").mockRejectedValue(new ApiError(409, "Conflict"));
  const page = mount(); await screen.findByLabelText("Cutoff date");
  fireEvent.click(screen.getByRole("button", { name: "Save packaging settings" }));
  await screen.findByText("Settings changed. Refresh before saving again.");
  expect(screen.getByRole("button", { name: "Reload settings" })).toBeEnabled();
  expect(requestNavigation("/materials")).toBe(true);
  page.unmount(); mount("LEADERSHIP");
  expect(await screen.findByLabelText("Cutoff date")).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Save packaging settings" })).not.toBeInTheDocument();
});

it("can reload settings after a load failure without keeping a redundant refresh action", async () => {
  const current = vi.spyOn(api, "current").mockRejectedValueOnce(new Error("Offline")).mockResolvedValue(initial);
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "Reload settings" }));
  await screen.findByLabelText("Cutoff date");
  expect(current).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: "Reload settings" })).not.toBeInTheDocument();
});
