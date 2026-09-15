// Tokens live only in memory. Cookies remain HttpOnly and browser-managed.
let csrfToken: string | null = null;
let generation = 0;
const listeners = new Set<(reason: "expired" | "password-required") => void>();

export function setSessionToken(token: string | null) {
  csrfToken = token;
  generation += 1;
}
export function sessionGeneration() { return generation; }
export function subscribeSessionInvalidation(listener: (reason: "expired" | "password-required") => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function notifySessionInvalidation(status: number, body: unknown, sentGeneration: number) {
  if (sentGeneration !== generation) return;
  const forced = status === 403 && typeof body === "object" && body !== null && "detail" in body &&
    typeof body.detail === "object" && body.detail !== null && "code" in body.detail &&
    body.detail.code === "PASSWORD_CHANGE_REQUIRED";
  if (status !== 401 && !forced) return;
  setSessionToken(null);
  for (const listener of listeners) listener(forced ? "password-required" : "expired");
}
export function authenticatedHeaders(method: string): Record<string, string> {
  return csrfToken && !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())
    ? { "X-CSRF-Token": csrfToken } : {};
}
export function apiUrl(path: string): string {
  const base = (import.meta.env.VITE_API_BASE_URL ?? "/api").replace(/\/$/, "");
  const target = base + path;
  // Never forward cookies or CSRF to a configured third-party API origin.
  if (new URL(target, window.location.href).origin !== window.location.origin) {
    throw new Error("The API must use the application origin.");
  }
  return target;
}
