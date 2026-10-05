import type { Role } from "./client";

// Presentation only. The server independently checks every operation.
// An absent role is used by isolated domain component tests, never the app entry.
export function restrictedDestination(path: string, role: Role | undefined): boolean {
  if (!role) return false;
  const manages = role === "ADMIN" || role === "PRODUCTION_LEAD";
  if (/^\/(companies|projects|customers|orders|materials)\/new$/.test(path) ||
      /^\/(companies|projects|customers|orders|brands)\/[^/]+\/edit$/.test(path) ||
      /^\/companies\/[^/]+\/brands\/new$/.test(path)) return !manages;
  if (/^\/materials\/[^/]+\/edit$/.test(path)) return role === "LEADERSHIP";
  if (path === "/imports" || path === "/settings/imports") return !manages;
  if (path === "/settings/users" || path === "/settings/paths") return role !== "ADMIN";
  if (path === "/material-archives" || path.startsWith("/material-archives/")) return role !== "ADMIN";
  if (path === "/publication") return role !== "ADMIN" && role !== "LEADERSHIP";
  return false;
}
