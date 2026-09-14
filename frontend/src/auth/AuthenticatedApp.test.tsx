import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthenticatedApp } from "./AuthenticatedApp";
import { createAuthClient } from "../api/authClient";
import { mockApiClient, type ApiClient } from "../api/client";
import { createHttpTransport } from "../api/transport";

const syntheticPassword = "Synthetic orchard phrase 927";
const replacement = "Synthetic river phrase 642";
const session = (mustChange = false, name = "Synthetic Reviewer") => ({
  user: { id: "10000000-0000-4000-8000-000000000001", display_name: name, email: "reviewer@example.invalid", role: "ADMIN" },
  must_change_password: mustChange,
  csrf_token: "synthetic-csrf-from-session",
});
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
function harness(path = "/dashboard", client: ApiClient = mockApiClient) {
  window.history.replaceState({}, "", path);
  const transport = createHttpTransport();
  const authClient = createAuthClient(transport);
  const fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
  const mount = () => render(<AuthenticatedApp client={client} transport={transport} authClient={authClient} />);
  return { transport, authClient, fetchMock, mount, client };
}
async function fillLogin() {
  await screen.findByRole("heading", { name: "Sign in" });
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "reviewer@example.invalid" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: syntheticPassword } });
}
function submitLogin() { fireEvent.click(screen.getByRole("button", { name: "Sign in" })); }
async function fillChange(confirmation = replacement) {
  await screen.findByRole("heading", { name: "Change password" });
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: syntheticPassword } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: replacement } });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: confirmation } });
}
function submitChange() { fireEvent.click(screen.getByRole("button", { name: "Change password" })); }
async function openMenu() { fireEvent.click(await screen.findByRole("button", { name: "User menu" })); }

beforeEach(() => {
  vi.stubGlobal("scrollTo", vi.fn());
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: function (this: HTMLDialogElement) {
    this.setAttribute("open", ""); this.querySelector<HTMLButtonElement>("button")?.focus();
  } });
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value: function (this: HTMLDialogElement) {
    this.removeAttribute("open"); this.dispatchEvent(new Event("close"));
  } });
});
afterEach(async () => { cleanup(); await Promise.resolve(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("production session gate", () => {
  it("shows a valid session and safely escaped profile without storing secrets", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const { fetchMock, mount } = harness();
    fetchMock.mockResolvedValue(response(session(false, "<img src=x onerror=alert(1)>")));
    mount();
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    await openMenu();
    const menu = screen.getByRole("dialog", { name: "Your account" });
    expect(within(menu).getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(within(menu).getByText("reviewer@example.invalid")).toBeInTheDocument();
    expect(within(menu).getByText("ADMIN")).toBeInTheDocument();
    expect(menu.querySelector("img")).toBeNull();
    expect(storage).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("synthetic-csrf-from-session");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/session", expect.objectContaining({ credentials: "include" }));
  });
  it("does not render or request protected data before session bootstrap finishes", async () => {
    const getCompanies = vi.fn().mockResolvedValue([]);
    const h = harness("/companies", { ...mockApiClient, getCompanies });
    const pending = deferred<Response>(); h.fetchMock.mockReturnValue(pending.promise); h.mount();
    expect(screen.getByRole("status")).toHaveTextContent("Checking your session");
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(getCompanies).not.toHaveBeenCalled();
    await act(async () => { pending.resolve(response({}, 401)); });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(getCompanies).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe("/login");
  });
  it("deduplicates StrictMode session bootstrap", async () => {
    const h = harness(); h.fetchMock.mockResolvedValue(response(session()));
    render(<StrictMode><AuthenticatedApp authClient={h.authClient} transport={h.transport} client={h.client} /></StrictMode>);
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(h.fetchMock).toHaveBeenCalledTimes(1);
  });
  it.each(["network", "503", "contract"])("offers a focused safe unavailable state and Retry for bootstrap %s", async (failure) => {
    const h = harness();
    if (failure === "network") h.fetchMock.mockRejectedValueOnce(new TypeError("sensitive-internal-detail"));
    else h.fetchMock.mockResolvedValueOnce(response(failure === "contract" ? { csrf_token: "sensitive-internal-detail" } : {}, failure === "503" ? 503 : 200));
    const pending = deferred<Response>(); h.fetchMock.mockReturnValueOnce(pending.promise); h.mount();
    expect(await screen.findByRole("heading", { name: "Service unavailable" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveFocus();
    expect(document.body.textContent).not.toContain("sensitive-internal-detail");
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    await act(async () => { pending.resolve(response(session())); });
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(h.fetchMock).toHaveBeenCalledTimes(2);
  });
  it("reloads session after a real root unmount rather than trusting the old profile", async () => {
    const h = harness(); h.fetchMock.mockResolvedValueOnce(response(session()));
    const view = h.mount(); await screen.findByRole("heading", { name: "Dashboard" });
    view.unmount(); await act(async () => { await Promise.resolve(); });
    h.fetchMock.mockResolvedValueOnce(response({}, 401)); h.mount();
    await screen.findByRole("heading", { name: "Sign in" });
    expect(h.fetchMock).toHaveBeenCalledTimes(2);
  });
  it("ignores the response belonging to an unmounted provider", async () => {
    const h = harness(); const old = deferred<Response>();
    h.fetchMock.mockReturnValueOnce(old.promise).mockResolvedValueOnce(response({}, 401));
    const view = h.mount(); await waitFor(() => expect(h.fetchMock).toHaveBeenCalledTimes(1));
    view.unmount(); await act(async () => { await Promise.resolve(); }); h.mount();
    await screen.findByRole("heading", { name: "Sign in" });
    await act(async () => { old.resolve(response(session())); });
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Dashboard" })).not.toBeInTheDocument();
  });
});

describe("login", () => {
  it("uses the fresh GET session, not the POST profile or CSRF, and a closed Dashboard redirect", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const h = harness("/login?returnTo=https://external.example.invalid&next=//external.example.invalid");
    h.fetchMock.mockResolvedValueOnce(response({}, 401)).mockResolvedValueOnce(response({ ...session(true, "Ignored POST profile"), csrf_token: "ignored-post-csrf" }))
      .mockResolvedValueOnce(response(session(false, "Fresh GET profile"))).mockResolvedValueOnce(response({ status: "logged_out" }));
    h.mount(); await fillLogin();
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "current-password");
    submitLogin(); await screen.findByRole("heading", { name: "Dashboard" });
    expect(screen.getByRole("heading", { name: "Dashboard" })).toHaveFocus();
    expect(window.location.pathname).toBe("/dashboard"); expect(window.location.search).toBe("");
    expect(screen.getByRole("button", { name: "User menu" })).toHaveTextContent("Fresh GET profile");
    expect(h.fetchMock.mock.calls.map(([url]) => url)).toEqual(["/api/auth/session", "/api/auth/login", "/api/auth/session"]);
    const loginOptions = h.fetchMock.mock.calls[1][1];
    expect(JSON.parse(String(loginOptions?.body))).toEqual({ email: "reviewer@example.invalid", password: syntheticPassword });
    expect(new Headers(loginOptions?.headers).has("X-CSRF-Token")).toBe(false);
    for (const [, options] of h.fetchMock.mock.calls) expect(options?.credentials).toBe("include");
    await openMenu(); fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByRole("heading", { name: "Sign in" });
    expect(new Headers(h.fetchMock.mock.calls[3][1]?.headers).get("X-CSRF-Token")).toBe("synthetic-csrf-from-session");
    expect(storage).not.toHaveBeenCalled();
  });
  it.each([
    [401, "The email or password is incorrect"], [403, "could not be authorized"],
    [422, "sign-in details were not accepted"], [429, "Too many attempts"], [503, "service is unavailable"],
  ])("safely handles login %i, clears passwords and focuses the summary", async (status, message) => {
    const h = harness(); h.fetchMock.mockResolvedValueOnce(response({}, 401)).mockResolvedValueOnce(response({
      detail: [{ input: syntheticPassword, ctx: { password: syntheticPassword }, msg: syntheticPassword }],
    }, status));
    h.mount(); await fillLogin(); submitLogin();
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("alert")).toHaveFocus();
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(document.body.textContent).not.toContain(syntheticPassword);
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
    expect(h.fetchMock).toHaveBeenCalledTimes(2);
  });
  it("handles login network failure without leaking the thrown error", async () => {
    const h = harness(); h.fetchMock.mockResolvedValueOnce(response({}, 401)).mockRejectedValueOnce(new TypeError(syntheticPassword));
    h.mount(); await fillLogin(); submitLogin();
    expect(await screen.findByRole("alert")).toHaveTextContent("service is unavailable");
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(document.body.textContent).not.toContain(syntheticPassword);
  });
  it("does not authenticate when POST succeeds but the cookie-backed session fails", async () => {
    const h = harness(); h.fetchMock.mockResolvedValueOnce(response({}, 401)).mockResolvedValueOnce(response(session()))
      .mockResolvedValueOnce(response({}, 503)).mockResolvedValueOnce(response(session()));
    h.mount(); await fillLogin(); submitLogin();
    await screen.findByRole("heading", { name: "Service unavailable" });
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(h.fetchMock.mock.calls.filter(([url]) => url === "/api/auth/login")).toHaveLength(1);
  });
  it("deduplicates double submission and disables controls", async () => {
    const h = harness(); const pending = deferred<Response>();
    h.fetchMock.mockResolvedValueOnce(response({}, 401)).mockReturnValueOnce(pending.promise).mockResolvedValueOnce(response(session()));
    h.mount(); await fillLogin();
    const form = screen.getByRole("form", { name: "Sign in" });
    fireEvent.submit(form); fireEvent.submit(form);
    expect(screen.getByRole("button", { name: /Signing in/ })).toBeDisabled();
    expect(screen.getByLabelText("Password")).toBeDisabled();
    await waitFor(() => expect(h.fetchMock).toHaveBeenCalledTimes(2));
    await act(async () => { pending.resolve(response(session())); });
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(h.fetchMock.mock.calls.filter(([url]) => url === "/api/auth/login")).toHaveLength(1);
  });
  it("stays anonymous with a blank password when post-login GET session returns 401", async () => {
    const h = harness(); h.fetchMock.mockResolvedValueOnce(response({}, 401)).mockResolvedValueOnce(response(session()))
      .mockResolvedValueOnce(response({}, 401));
    h.mount(); await fillLogin(); submitLogin();
    await screen.findByText("Sign-in could not be verified. Please sign in again.");
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(h.fetchMock).toHaveBeenCalledTimes(3);
  });
});

describe("mandatory password change", () => {
  it.each(["/dashboard", "/companies", "/projects", "/materials", "/settings", "/login"])("blocks workspace route %s", async (path) => {
    const h = harness(path); h.fetchMock.mockResolvedValue(response(session(true))); h.mount();
    await screen.findByRole("heading", { name: "Change password" });
    expect(window.location.pathname).toBe("/change-password");
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Current password")).toHaveFocus();
    expect(h.fetchMock).toHaveBeenCalledTimes(1);
    act(() => { window.history.pushState({}, "", "/materials"); window.dispatchEvent(new PopStateEvent("popstate")); });
    expect(window.location.pathname).toBe("/change-password");
  });
  it("changes once with CSRF, sends no confirmation, clears session and requires a new login", async () => {
    const h = harness(); const pending = deferred<Response>();
    h.fetchMock.mockResolvedValueOnce(response(session(true))).mockReturnValueOnce(pending.promise).mockResolvedValueOnce(response({}));
    h.mount(); await fillChange();
    expect(screen.getByLabelText("Current password")).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByLabelText("New password")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Confirm new password")).toHaveAttribute("autocomplete", "new-password");
    const form = screen.getByRole("form", { name: "Change password" }); fireEvent.submit(form); fireEvent.submit(form);
    expect(screen.getByRole("button", { name: /Changing password/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "User menu" })).toBeDisabled();
    await act(async () => { pending.resolve(response({ status: "password_changed", reauthentication_required: true, changed_at: "2026-09-14T10:00:00Z" })); });
    await screen.findByRole("heading", { name: "Sign in" });
    expect(screen.getByText(/Your password was changed/)).toHaveAttribute("role", "status");
    expect(h.fetchMock).toHaveBeenCalledTimes(2);
    const options = h.fetchMock.mock.calls[1][1];
    expect(JSON.parse(String(options?.body))).toEqual({ current_password: syntheticPassword, new_password: replacement });
    expect(new Headers(options?.headers).get("X-CSRF-Token")).toBe("synthetic-csrf-from-session");
    await h.transport.request("/synthetic-resource", { method: "POST" });
    expect(new Headers(h.fetchMock.mock.calls[2][1]?.headers).has("X-CSRF-Token")).toBe(false);
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });
  it("rejects mismatching confirmation locally and focuses the error", async () => {
    const h = harness(); h.fetchMock.mockResolvedValueOnce(response(session(true))); h.mount();
    await fillChange("Different synthetic phrase"); submitChange();
    expect(screen.getByRole("alert")).toHaveTextContent("do not match");
    expect(screen.getByRole("alert")).toHaveFocus();
    expect(h.fetchMock).toHaveBeenCalledTimes(1);
  });
  it("blocks password change while logout is in progress, even after closing the dialog", async () => {
    const h = harness(); const pending = deferred<Response>();
    h.fetchMock.mockResolvedValueOnce(response(session(true))).mockReturnValueOnce(pending.promise);
    h.mount(); await fillChange(); await openMenu();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.getByRole("button", { name: "Change password" })).toBeDisabled();
    expect(screen.getByLabelText("New password")).toBeDisabled();
    fireEvent.submit(screen.getByRole("form", { name: "Change password" }));
    expect(h.fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => { pending.resolve(response({}, 503)); });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.getByRole("button", { name: "Change password" })).toBeEnabled();
  });
  it("returns to login on change-password 401", async () => {
    const h = harness(); h.fetchMock.mockResolvedValueOnce(response(session(true))).mockResolvedValueOnce(response({}, 401));
    h.mount(); await fillChange(); submitChange();
    await screen.findByRole("heading", { name: "Sign in" });
    expect(screen.getByText(/Your session has expired/)).toBeInTheDocument();
  });
  it.each([[400, "current password is incorrect"], [422, "passwords were not accepted"]])("handles change-password %i safely", async (status, message) => {
    const h = harness(); h.fetchMock.mockResolvedValueOnce(response(session(true))).mockResolvedValueOnce(response({ detail: replacement, input: syntheticPassword }, status));
    h.mount(); await fillChange(); submitChange();
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("alert")).toHaveFocus();
    for (const label of ["Current password", "New password", "Confirm new password"]) expect(screen.getByLabelText(label)).toHaveValue("");
    expect(document.body.textContent).not.toContain(syntheticPassword); expect(document.body.textContent).not.toContain(replacement);
  });
});

describe("logout and session expiry", () => {
  it.each([200, 401])("ends local session on logout %i with real CSRF and no duplicate request", async (status) => {
    const h = harness(); const pending = deferred<Response>();
    h.fetchMock.mockResolvedValueOnce(response(session())).mockReturnValueOnce(pending.promise); h.mount(); await openMenu();
    const button = screen.getByRole("button", { name: "Sign out" }); fireEvent.click(button); fireEvent.click(button);
    expect(screen.getByRole("button", { name: /Signing out/ })).toBeDisabled();
    await act(async () => { pending.resolve(response(status === 200 ? { status: "logged_out" } : {}, status)); });
    await screen.findByRole("heading", { name: "Sign in" });
    expect(h.fetchMock).toHaveBeenCalledTimes(2);
    expect(h.fetchMock.mock.calls[1][0]).toBe("/api/auth/logout");
    expect(new Headers(h.fetchMock.mock.calls[1][1]?.headers).get("X-CSRF-Token")).toBe("synthetic-csrf-from-session");
  });
  it("keeps the session on logout network failure, focuses the error and supports retry", async () => {
    const h = harness(); h.fetchMock.mockResolvedValueOnce(response(session())).mockRejectedValueOnce(new TypeError(syntheticPassword))
      .mockResolvedValueOnce(response({ status: "logged_out" }));
    h.mount(); await openMenu(); fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("server session may still be active");
    expect(screen.getByRole("alert")).toHaveFocus();
    expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(syntheticPassword);
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByRole("heading", { name: "Sign in" });
  });
  it("closes the account dialog using Escape and returns focus", async () => {
    const h = harness(); h.fetchMock.mockResolvedValueOnce(response(session())); h.mount(); await openMenu();
    const menu = screen.getByRole("dialog", { name: "Your account" });
    fireEvent(menu, new Event("cancel", { cancelable: true }));
    expect(menu).not.toHaveAttribute("open"); expect(screen.getByRole("button", { name: "User menu" })).toHaveFocus();
  });
  it("allows Escape during logout and reopens a focused error if the server fails", async () => {
    const h = harness(); const pending = deferred<Response>();
    h.fetchMock.mockResolvedValueOnce(response(session())).mockReturnValueOnce(pending.promise);
    h.mount(); await openMenu();
    const menu = screen.getByRole("dialog", { name: "Your account" });
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(screen.getByRole("button", { name: "Close" })).toBeEnabled();
    fireEvent(menu, new Event("cancel", { cancelable: true }));
    expect(menu).not.toHaveAttribute("open"); expect(screen.getByRole("button", { name: "User menu" })).toHaveFocus();
    await act(async () => { pending.resolve(response({}, 503)); });
    expect(screen.getByRole("dialog", { name: "Your account" })).toBeVisible();
    expect(screen.getByRole("alert")).toHaveFocus();
    expect(screen.getByRole("alert")).toHaveTextContent("server session may still be active");
    expect(h.fetchMock).toHaveBeenCalledTimes(2);
  });
  it("expires on a resource 401, makes no bootstrap loop and ignores another old 401 after new login", async () => {
    const h = harness("/companies"); const old = deferred<Response>();
    const resourceClient = { ...mockApiClient, getCompanies: () => h.transport.request("/companies").then(() => []) };
    h.fetchMock.mockResolvedValueOnce(response(session())).mockResolvedValueOnce(response({}, 401))
      .mockReturnValueOnce(old.promise).mockResolvedValueOnce(response(session())).mockResolvedValueOnce(response(session()));
    render(<AuthenticatedApp client={resourceClient} transport={h.transport} authClient={h.authClient} />);
    await screen.findByRole("heading", { name: "Sign in" });
    expect(screen.getByText(/Your session has expired/)).toBeInTheDocument();
    expect(h.fetchMock).toHaveBeenCalledTimes(2);
    const late = h.transport.request("/old-resource").catch(() => undefined);
    await fillLogin(); submitLogin(); await screen.findByRole("heading", { name: "Dashboard" });
    await act(async () => { old.resolve(response({}, 401)); await late; });
    expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(h.fetchMock.mock.calls.filter(([url]) => url === "/api/auth/session")).toHaveLength(2);
  });
});
