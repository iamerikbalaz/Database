import { apiUrl, authenticatedHeaders } from "./sessionTransport";
import { uuid } from "../api/dto";

export type Role = "PROCESSOR" | "PRODUCTION_LEAD" | "LEADERSHIP" | "ADMIN";
export interface User { id: string; display_name: string; email: string; role: Role }
export interface AuthSession { user: User; must_change_password: boolean; csrf_token: string }
export class AuthError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = "AuthError"; }
}
function parseSession(value: unknown): AuthSession {
  if (!value || typeof value !== "object" || !("user" in value) ||
      !value.user || typeof value.user !== "object" || !("must_change_password" in value) ||
      typeof value.must_change_password !== "boolean" || !("csrf_token" in value) ||
      typeof value.csrf_token !== "string" || !/^[A-Za-z0-9_-]{43,128}$/.test(value.csrf_token)) {
    throw new AuthError(502, "The server returned an invalid session. Try again.");
  }
  const user = value.user;
  if (!("id" in user) || typeof user.id !== "string" ||
      !("display_name" in user) || typeof user.display_name !== "string" ||
      !("email" in user) || typeof user.email !== "string" ||
      !("role" in user) || !["PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP", "ADMIN"].includes(String(user.role))) {
    throw new AuthError(502, "The server returned an invalid account. Try again.");
  }
  return { user: { id: user.id, display_name: user.display_name, email: user.email, role: user.role as Role },
    must_change_password: value.must_change_password, csrf_token: value.csrf_token };
}

async function authRequest(path: string, method: string, payload?: object): Promise<unknown> {
  const response = await fetch(apiUrl("/auth" + path), { method, credentials: "same-origin", cache: "no-store",
    headers: { Accept: "application/json", ...authenticatedHeaders(method),
      ...(payload ? { "Content-Type": "application/json" } : {}) },
    body: payload ? JSON.stringify(payload) : undefined,
  });
  if (!response.ok) {
    // Allowlisted messages: never render reflected request/password/token data.
    const message = response.status === 401 ? "Invalid email or password, or your session has expired."
      : response.status === 429 ? "Too many sign-in attempts. Wait a few minutes and try again."
      : response.status === 400 ? "The current password is incorrect."
      : response.status === 422 ? "Use a different password of 15–256 characters. Avoid common passwords and account details."
      : response.status === 403 ? "The request could not be verified. Reload this page and try again."
      : "The authentication service is unavailable. Try again.";
    throw new AuthError(response.status, message);
  }
  return response.json();
}

export const authClient = {
  async session(): Promise<AuthSession> { return parseSession(await authRequest("/session", "GET")); },
  async login(email: string, password: string): Promise<AuthSession> {
    return parseSession(await authRequest("/login", "POST", { email, password }));
  },
  async logout(): Promise<void> { await authRequest("/logout", "POST"); },
  async changePassword(current_password: string, new_password: string): Promise<void> {
    await authRequest("/change-password", "POST", { current_password, new_password });
  },
  async provisionAccess(userId: string, current_password: string, new_password: string): Promise<void> {
    await authRequest(`/accounts/${uuid(userId)}/access`, "POST", { current_password, new_password });
  },
};
