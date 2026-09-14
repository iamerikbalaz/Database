import { useLayoutEffect, useRef, useSyncExternalStore, type ReactNode } from "react";
import App from "../App";
import { authApiClient, type AuthClient } from "../api/authClient";
import { httpTransport, type HttpTransport } from "../api/transport";
import type { ApiClient } from "../api/client";
import { SessionProvider } from "./SessionProvider";
import { useSession } from "./sessionContext";
import { AuthForm } from "./AuthForm";
import { UserMenu } from "./UserMenu";

function subscribePath(listener: () => void) {
  window.addEventListener("popstate", listener);
  return () => window.removeEventListener("popstate", listener);
}
const currentPath = () => window.location.pathname;

function AuthLayout({ children }: { children: ReactNode }) {
  return <main className="auth-layout"><section className="auth-card panel">
    <div className="brand auth-brand"><span className="brand-mark">R</span><span>REAWOTE</span></div>
    {children}
  </section></main>;
}
function Redirect({ path }: { path: "/login" | "/change-password" | "/dashboard" }) {
  useLayoutEffect(() => {
    // Destinations are closed literals, never a return URL from query parameters.
    window.history.replaceState({}, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, [path]);
  return <AuthLayout><p role="status">Opening your workspace…</p></AuthLayout>;
}
function Unavailable({ message, retry }: { message: string; retry: () => void }) {
  const summary = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => { summary.current?.focus(); }, [message]);
  return <AuthLayout><h1>Service unavailable</h1>
    <div ref={summary} className="form-error" role="alert" tabIndex={-1}><p>{message}</p></div>
    <button className="button button--primary" type="button" onClick={retry}>Retry</button>
  </AuthLayout>;
}
function AuthenticatedWorkspace({ client }: { client?: ApiClient }) {
  useLayoutEffect(() => {
    const heading = document.querySelector<HTMLElement>(".content h1");
    if (heading) { heading.tabIndex = -1; heading.focus(); }
  }, []);
  return <App client={client} userMenu={<UserMenu />} />;
}
function SessionGate({ client }: { client?: ApiClient }) {
  const { state, store } = useSession();
  const path = useSyncExternalStore(subscribePath, currentPath);
  if (state.status === "loading") return <AuthLayout><h1>REAWOTE workspace</h1><p role="status">Checking your session…</p></AuthLayout>;
  if (state.status === "unavailable") return <Unavailable message={state.message} retry={() => void store.refresh()} />;
  if (state.status === "anonymous") {
    if (path !== "/login") return <Redirect path="/login" />;
    return <AuthLayout><p role="status" className="auth-notice">{state.notice}</p><AuthForm mode="login" /></AuthLayout>;
  }
  if (state.session.mustChangePassword) {
    if (path !== "/change-password") return <Redirect path="/change-password" />;
    return <AuthLayout><AuthForm mode="change" /><UserMenu /></AuthLayout>;
  }
  if (path === "/login" || path === "/change-password") return <Redirect path="/dashboard" />;
  return <AuthenticatedWorkspace key={state.session.user.id} client={client} />;
}

export function AuthenticatedApp({ client, authClient = authApiClient, transport = httpTransport }: {
  client?: ApiClient; authClient?: AuthClient; transport?: HttpTransport;
}) {
  return <SessionProvider client={authClient} transport={transport}><SessionGate client={client} /></SessionProvider>;
}
