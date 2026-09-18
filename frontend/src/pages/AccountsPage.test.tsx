import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AccountsPage } from "./AccountsPage";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import { processorDto } from "../test/materialFixtures";
import type { AuthSession } from "../auth/client";

const admin = { ...processorDto, id: "00000000-0000-4000-8000-000000000001", display_name: "Site Operator", email: "operator@example.invalid", role: "ADMIN" as const };
const auth: AuthSession = { user: admin, csrf_token: "a".repeat(43), must_change_password: false };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
function mount() {
  setSessionToken(auth.csrf_token);
  render(<SessionContext.Provider value={{ session: auth, pending: false, logout: vi.fn(), changePassword: vi.fn() }}><AccountsPage navigate={vi.fn()} /></SessionContext.Provider>);
}
function rows() { return [admin, processorDto]; }

it("creates a profile with the least privileged role and explains the remaining access step", async () => {
  const items = rows();
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      const created = { ...processorDto, ...body, id: "00000000-0000-4000-8000-000000000099" };
      items.push(created); return json(created, 201);
    }
    return json(items);
  });
  vi.stubGlobal("fetch", fetch); mount();
  const form = screen.getByRole("form", { name: "Create account" });
  expect(within(form).getByLabelText("Role")).toHaveValue("PROCESSOR");
  fireEvent.change(within(form).getByLabelText("Display name"), { target: { value: "New Person" } });
  fireEvent.change(within(form).getByLabelText("Email"), { target: { value: "new@example.invalid" } });
  fireEvent.submit(form);
  expect(await screen.findByText("Profile created. Use Set or reset access to issue a temporary password.")).toHaveAttribute("role", "status");
  expect(fetch).toHaveBeenCalledWith("/api/internal-users", expect.objectContaining({ method: "POST", body: JSON.stringify({ display_name: "New Person", email: "new@example.invalid", role: "PROCESSOR" }), headers: expect.objectContaining({ "X-CSRF-Token": auth.csrf_token }) }));
  await screen.findByRole("form", { name: "Manage New Person" });
});

it("preserves inputs when creating a duplicate account fails", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init?: RequestInit) => init?.method === "POST" ? json({ detail: "email already exists." }, 409) : json(rows())));
  mount(); const form = screen.getByRole("form", { name: "Create account" });
  fireEvent.change(within(form).getByLabelText("Display name"), { target: { value: "Existing" } });
  fireEvent.change(within(form).getByLabelText("Email"), { target: { value: "existing@example.invalid" } });
  fireEvent.submit(form);
  expect(await screen.findByRole("alert")).toHaveTextContent("email already exists");
  expect(within(form).getByLabelText("Email")).toHaveValue("existing@example.invalid");
});

it("protects self-demotion and sends only role and active state for another account", async () => {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => init?.method === "PATCH" ? json({ ...processorDto, ...JSON.parse(String(init.body)) }) : json(rows()));
  vi.stubGlobal("fetch", fetch); mount();
  const self = await screen.findByRole("form", { name: "Manage Site Operator" });
  expect(within(self).getByLabelText("Role")).toBeDisabled();
  expect(within(self).getByLabelText("Active")).toBeDisabled();
  expect(within(self).queryByRole("button", { name: "Set or reset access" })).not.toBeInTheDocument();
  const target = screen.getByRole("form", { name: `Manage ${processorDto.display_name}` });
  fireEvent.change(within(target).getByLabelText("Role"), { target: { value: "PRODUCTION_LEAD" } });
  fireEvent.click(within(target).getByLabelText("Active"));
  fireEvent.submit(target);
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(`/api/internal-users/${processorDto.id}`, expect.objectContaining({ method: "PATCH", body: JSON.stringify({ role: "PRODUCTION_LEAD", is_active: false }) })));
});

it("confirms temporary access and clears all password fields on failure", async () => {
  const fetch = vi.fn(async (path: string) => path.includes("/access") ? json({ detail: "SECRET_REFLECTION" }, 400) : json(rows()));
  vi.stubGlobal("fetch", fetch); mount();
  const target = await screen.findByRole("form", { name: `Manage ${processorDto.display_name}` });
  fireEvent.click(within(target).getByRole("button", { name: "Set or reset access" }));
  const form = screen.getByRole("form", { name: "Set temporary access" });
  fireEvent.change(within(form).getByLabelText("Your current password"), { target: { value: "Synthetic admin password 2026" } });
  fireEvent.change(within(form).getByLabelText("Temporary password"), { target: { value: "Synthetic initial password 2026" } });
  fireEvent.change(within(form).getByLabelText("Confirm temporary password"), { target: { value: "different" } });
  fireEvent.submit(form);
  expect(screen.getByRole("alert")).toHaveTextContent("do not match");
  expect(fetch.mock.calls.filter(([path]) => path.includes("/access"))).toHaveLength(0);
  fireEvent.change(within(form).getByLabelText("Confirm temporary password"), { target: { value: "Synthetic initial password 2026" } });
  fireEvent.submit(form);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("current password is incorrect"));
  expect(screen.getByRole("alert")).not.toHaveTextContent("SECRET_REFLECTION");
  for (const label of ["Your current password", "Temporary password", "Confirm temporary password"]) expect(within(form).getByLabelText(label)).toHaveValue("");
});
