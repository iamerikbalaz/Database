import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import type { AuthClient } from "../api/authClient";
import type { HttpTransport } from "../api/transport";
import { SessionContext } from "./sessionContext";
import { SessionStore } from "./sessionStore";

export function SessionProvider({ client, transport, children }: {
  client: AuthClient; transport: HttpTransport; children: ReactNode;
}) {
  const [store] = useState(() => new SessionStore(client, transport));
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  useEffect(() => store.connect(), [store]);
  return <SessionContext.Provider value={{ state, store }}>{children}</SessionContext.Provider>;
}
