import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthenticatedApp } from "./AuthenticatedApp";
import { httpApiClient } from "../api/client";
import { apiUrl, authenticatedHeaders, notifySessionInvalidation, sessionGeneration, setSessionToken } from "./sessionTransport";

const session = { user: { id: "00000000-0000-4000-8000-000000000001", email: "test@example.invalid", display_name: "Test User", role: "ADMIN" }, must_change_password: false, csrf_token: "a".repeat(43) };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => { setSessionToken(null); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });
function fillLogin() {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: session.user.email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "Quartz meadow river! 2026" } });
}
function serveSession(forced = false) {
  const fetch = vi.fn(async (path: string) => path === "/api/auth/session" ? json({ ...session, must_change_password: forced }) : json([]));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

it("waits for session bootstrap before mounting the workspace and never persists tokens", async () => {
  let resolve!: (value: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
  const storage = vi.spyOn(Storage.prototype, "setItem");
  render(<AuthenticatedApp />);
  expect(screen.getByRole("status")).toHaveTextContent("Checking your session");
  expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  await act(async () => resolve(json(session)));
  expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
  expect(authenticatedHeaders("PATCH")).toEqual({ "X-CSRF-Token": session.csrf_token });
  expect(authenticatedHeaders("GET")).toEqual({});
  expect(storage).not.toHaveBeenCalled();
});

it("survives StrictMode double bootstrap and ignores an older response", async () => {
  const resolves: ((value: Response) => void)[] = [];
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => resolves.push(resolve))));
  render(<StrictMode><AuthenticatedApp /></StrictMode>);
  expect(resolves).toHaveLength(2);
  await act(async () => resolves[1](json(session)));
  await act(async () => resolves[0](json({}, 401)));
  expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
  expect(authenticatedHeaders("POST")).toEqual({ "X-CSRF-Token": session.csrf_token });
});

it("signs in with one request, preserves the destination and clears the password", async () => {
  window.history.replaceState({}, "", "/projects");
  let resolve!: (value: Response) => void;
  const fetch = vi.fn((path: string) => path === "/api/auth/session" ? Promise.resolve(json({}, 401))
    : path === "/api/auth/login" ? new Promise<Response>((done) => { resolve = done; }) : Promise.resolve(json([])));
  vi.stubGlobal("fetch", fetch);
  render(<AuthenticatedApp />);
  await screen.findByRole("heading", { name: "Sign in" });
  fillLogin();
  const form = screen.getByRole("form", { name: "Sign in" });
  fireEvent.submit(form); fireEvent.submit(form);
  expect(fetch.mock.calls.filter(([path]) => path === "/api/auth/login")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Signing in…" })).toBeDisabled();
  await act(async () => resolve(json(session)));
  expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
  expect(window.location.pathname).toBe("/projects");
  expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
});

it.each([401, 429, 503])("shows a safe focused sign-in failure (%s)", async (status) => {
  const fetch = vi.fn(async (path: string) => path === "/api/auth/session" ? json({}, 401) : json({ detail: "SECRET_REFLECTION" }, status));
  vi.stubGlobal("fetch", fetch);
  render(<AuthenticatedApp />);
  await screen.findByRole("heading", { name: "Sign in" }); fillLogin();
  fireEvent.submit(screen.getByRole("form", { name: "Sign in" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveFocus(); expect(alert).not.toHaveTextContent("SECRET_REFLECTION");
  expect(screen.getByLabelText("Password")).toHaveValue("");
  expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
});

it("retries bootstrap after a network failure", async () => {
  const fetch = vi.fn().mockRejectedValueOnce(new TypeError("network")).mockResolvedValueOnce(json({}, 401));
  vi.stubGlobal("fetch", fetch); render(<AuthenticatedApp />);
  expect(await screen.findByRole("heading", { name: "Connection unavailable" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
});

it("rejects malformed session data without mounting the workspace", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ ...session, user: { ...session.user, role: "UNKNOWN" } })));
  render(<AuthenticatedApp />);
  expect(await screen.findByRole("alert")).toHaveTextContent("invalid account");
  expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
});

it("forces password change, checks confirmation, submits CSRF and requires reauthentication", async () => {
  const fetch = serveSession(true);
  render(<AuthenticatedApp />);
  await screen.findByRole("heading", { name: "Change password" });
  expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Back to workspace" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "Quartz meadow river! 2026" } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: "Violet harbor lanterns 2048" } });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "different" } });
  fireEvent.submit(screen.getByRole("form", { name: "Change password" }));
  expect(screen.getByRole("alert")).toHaveTextContent("do not match");
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "Violet harbor lanterns 2048" } });
  fireEvent.submit(screen.getByRole("form", { name: "Change password" }));
  await screen.findByRole("heading", { name: "Sign in" });
  expect(screen.getByRole("status")).toHaveTextContent("Password changed");
  expect(fetch).toHaveBeenCalledWith("/api/auth/change-password", expect.objectContaining({
    method: "POST", credentials: "same-origin", headers: expect.objectContaining({ "X-CSRF-Token": session.csrf_token }),
  }));
  expect(authenticatedHeaders("POST")).toEqual({});
});

it("signs out using the session token and removes the workspace", async () => {
  const fetch = serveSession(); render(<AuthenticatedApp />);
  await screen.findByRole("navigation", { name: "Main navigation" });
  fireEvent.click(screen.getByLabelText("User menu"));
  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
  await screen.findByRole("heading", { name: "Sign in" });
  expect(fetch).toHaveBeenCalledWith("/api/auth/logout", expect.objectContaining({ headers: expect.objectContaining({ "X-CSRF-Token": session.csrf_token }) }));
  expect(authenticatedHeaders("POST")).toEqual({});
});

it("keeps the session visible when logout fails so it can be retried", async () => {
  const fetch = serveSession(); render(<AuthenticatedApp />);
  await screen.findByRole("navigation", { name: "Main navigation" });
  fetch.mockResolvedValueOnce(json({}, 503));
  fireEvent.click(screen.getByLabelText("User menu")); fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");
  expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
});

it("handles an expired domain session and ignores stale failures from an older login", async () => {
  const fetch = serveSession(); render(<AuthenticatedApp />);
  await screen.findByRole("navigation", { name: "Main navigation" });
  const stale = sessionGeneration(); setSessionToken(session.csrf_token);
  act(() => notifySessionInvalidation(401, {}, stale));
  expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
  fetch.mockResolvedValueOnce(json({}, 401));
  await act(async () => { await expect(httpApiClient.getCompanies()).rejects.toThrow(); });
  expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("expired");
});

it("reloads the password-change requirement received from the server", async () => {
  const fetch = serveSession(); render(<AuthenticatedApp />);
  await screen.findByRole("navigation", { name: "Main navigation" });
  fetch.mockResolvedValueOnce(json({ ...session, must_change_password: true }));
  act(() => notifySessionInvalidation(403, { detail: { code: "PASSWORD_CHANGE_REQUIRED" } }, sessionGeneration()));
  await screen.findByRole("heading", { name: "Change password" });
});

it("never forwards CSRF to a different configured origin", () => {
  vi.stubEnv("VITE_API_BASE_URL", "https://untrusted.invalid/api");
  expect(() => apiUrl("/companies")).toThrow("application origin");
});

it("sends CSRF for a domain write without placing it in the URL or storage", async () => {
  setSessionToken(session.csrf_token);
  const fetch = vi.fn(async () => json({}, 409)); vi.stubGlobal("fetch", fetch);
  await expect(httpApiClient.createCompany({ name: "Synthetic" })).rejects.toThrow();
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/companies", expect.objectContaining({ method: "POST", credentials: "same-origin", headers: expect.objectContaining({ "X-CSRF-Token": session.csrf_token }) })));
});

it.each(["PROCESSOR", "LEADERSHIP"])("hides catalog writes and rejects direct editor navigation for %s", async (role) => {
  window.history.replaceState({}, "", "/companies");
  vi.stubGlobal("fetch", vi.fn(async (path: string) => path === "/api/auth/session"
    ? json({ ...session, user: { ...session.user, role } }) : json([])));
  const view = render(<AuthenticatedApp />);
  await screen.findByRole("heading", { name: "Companies" });
  expect(screen.queryByRole("link", { name: "Add company" })).not.toBeInTheDocument();
  view.unmount();
  window.history.replaceState({}, "", "/companies/new");
  render(<AuthenticatedApp />);
  await screen.findByRole("heading", { name: "Access restricted" });
  expect(screen.queryByRole("form")).not.toBeInTheDocument();
});
