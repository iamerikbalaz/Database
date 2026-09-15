import { createContext, useContext } from "react";
import type { AuthSession } from "./client";

export interface SessionContextValue {
  session: AuthSession;
  pending: boolean;
  logout: () => void;
  changePassword: () => void;
}
export const SessionContext = createContext<SessionContextValue | null>(null);
export function useSession() { return useContext(SessionContext); }
