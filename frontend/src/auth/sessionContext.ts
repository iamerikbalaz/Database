import { createContext, useContext } from "react";
import type { SessionState, SessionStore } from "./sessionStore";

export const SessionContext = createContext<{ state: SessionState; store: SessionStore } | null>(null);
export function useSession() {
  const context = useContext(SessionContext);
  if (!context) throw new Error("Session provider is required.");
  return context;
}
