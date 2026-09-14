import { AuthError } from "../api/authClient";

export function authMessage(error: unknown, action: "login" | "change" | "logout" | "session"): string {
  if (!(error instanceof AuthError) || error.kind === "network") {
    return action === "logout"
      ? "Sign-out could not be confirmed. Your server session may still be active. Check your connection and try again."
      : "The service is unavailable. Check your connection and try again.";
  }
  if (error.kind === "contract") return "The service returned an unexpected response. Please try again.";
  if (error.status === 401) return action === "login"
    ? "The email or password is incorrect. Please try again."
    : "Your session has expired. Please sign in again.";
  if (error.status === 403) return "This action could not be authorized. Reload the page and try again.";
  if (error.status === 429) return "Too many attempts. Please wait before trying again.";
  if (error.status === 400 && action === "change") return "The current password is incorrect. Please try again.";
  if (error.status === 422) return action === "change"
    ? "The passwords were not accepted. Check the password requirements and try again."
    : "The sign-in details were not accepted. Check your email and password and try again.";
  return action === "logout"
    ? "Sign-out could not be confirmed. Your server session may still be active. Please try again."
    : "The service is unavailable. Please try again later.";
}
