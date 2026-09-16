import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import App from "../App";
import { authClient, AuthError, type AuthSession } from "./client";
import { SessionContext } from "./context";
import { setSessionToken, subscribeSessionInvalidation } from "./sessionTransport";

function errorMessage(error: unknown) {
  return error instanceof AuthError ? error.message : "Could not reach the server. Check your connection and try again.";
}

export function AuthenticatedApp() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [passwordPage, setPasswordPage] = useState(false);
  const [pending, setPending] = useState(false);
  const generation = useRef(0);
  const channel = useRef<BroadcastChannel | null>(null);
  const alert = useRef<HTMLParagraphElement>(null);
  const applySession = useCallback((value: AuthSession | null) => {
    setSessionToken(value?.csrf_token ?? null);
    setSession(value);
    setPasswordPage(Boolean(value?.must_change_password));
    setState("ready");
  }, []);
  const bootstrap = useCallback(() => {
    const current = ++generation.current;
    return authClient.session().then((value) => {
      if (generation.current === current) { setError(""); applySession(value); }
    }, (cause: unknown) => {
      if (generation.current !== current) return;
      if (cause instanceof AuthError && cause.status === 401) { setError(""); applySession(null); }
      else { setSessionToken(null); setSession(null); setError(errorMessage(cause)); setState("error"); }
    });
  }, [applySession]);
  useEffect(() => {
    void bootstrap();
    const unsubscribe = subscribeSessionInvalidation((reason) => {
      generation.current += 1;
      if (reason === "password-required") { setState("loading"); void bootstrap(); }
      else { applySession(null); setNotice("Your session has expired. Sign in again."); }
    });
    if (typeof BroadcastChannel !== "undefined") {
      const connection = new BroadcastChannel("reawote-auth");
      channel.current = connection;
      connection.onmessage = () => { setState("loading"); void bootstrap(); };
    }
    return () => {
      generation.current += 1;
      unsubscribe(); channel.current?.close(); channel.current = null; setSessionToken(null);
    };
  }, [applySession, bootstrap]);
  useLayoutEffect(() => { if (error) alert.current?.focus(); }, [error]);

  const logout = async () => {
    if (pending) return;
    setPending(true); setError("");
    try {
      await authClient.logout();
      generation.current += 1; applySession(null); setNotice("You have signed out.");
      channel.current?.postMessage("changed");
    } catch (cause) {
      if (cause instanceof AuthError && cause.status === 401) { applySession(null); }
      else setError(errorMessage(cause));
    } finally { setPending(false); }
  };
  const login = async (email: string, password: string) => {
    if (pending) return;
    const current = ++generation.current;
    setPending(true); setError(""); setNotice("");
    try {
      const value = await authClient.login(email, password);
      if (generation.current === current) { applySession(value); channel.current?.postMessage("changed"); }
    } catch (cause) { if (generation.current === current) setError(errorMessage(cause)); }
    finally { setPending(false); }
  };
  const changePassword = async (current: string, next: string) => {
    if (pending) return;
    setPending(true); setError("");
    try {
      await authClient.changePassword(current, next);
      generation.current += 1; applySession(null);
      setNotice("Password changed. Sign in with your new password.");
      channel.current?.postMessage("changed");
    } catch (cause) {
      if (cause instanceof AuthError && cause.status === 401) applySession(null);
      setError(errorMessage(cause));
    } finally { setPending(false); }
  };
  const errorAlert = error && <p ref={alert} tabIndex={-1} role="alert" className="field-error">{error}</p>;
  if (state === "loading") return <main className="auth-screen"><p role="status">Checking your session…</p></main>;
  if (state === "error") return <main className="auth-screen"><section className="auth-card"><h1>Connection unavailable</h1>{errorAlert}<button className="button" onClick={() => { setState("loading"); void bootstrap(); }}>Try again</button></section></main>;
  if (!session || passwordPage) return <main className="auth-screen"><section className="auth-card">
    <a className="auth-brand" href="/">REAWOTE<span>Internal workspace</span></a>
    {session ? <>
      <h1>Change password</h1>
      <p>{session.must_change_password ? "Set a personal password before continuing." : "Changing your password signs out all your sessions."}</p>
    </> : <><h1>Sign in</h1><p>Use your internal REAWOTE account.</p></>}
    {notice && <p role="status">{notice}</p>}{errorAlert}
    {session ? <PasswordForm pending={pending} onSubmit={changePassword} /> : <LoginForm pending={pending} onSubmit={login} />}
    {session && <div className="auth-actions">
      {!session.must_change_password && <button className="button" disabled={pending} onClick={() => { setPasswordPage(false); setError(""); }}>Back to workspace</button>}
      <button className="button" disabled={pending} onClick={() => void logout()}>Sign out</button>
    </div>}
    {!session && <p className="auth-help">For an account or password reset, contact your administrator.</p>}
  </section></main>;
  return <SessionContext.Provider value={{ session, pending, logout: () => void logout(), changePassword: () => { setPasswordPage(true); setError(""); } }}>
    {error && <div className="auth-global-error">{errorAlert}</div>}
    <App />
  </SessionContext.Provider>;
}

function LoginForm({ pending, onSubmit }: { pending: boolean; onSubmit: (email: string, password: string) => Promise<void> }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const submitted = useRef(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (submitted.current || pending) return;
    submitted.current = true;
    try { await onSubmit(email.trim(), password); } finally { setPassword(""); submitted.current = false; }
  };
  return <form className="auth-form" onSubmit={submit} aria-label="Sign in">
    <label>Email<input name="email" type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} disabled={pending} /></label>
    <label>Password<input name="password" type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} disabled={pending} /></label>
    <button className="button button--primary" disabled={pending}>{pending ? "Signing in…" : "Sign in"}</button>
  </form>;
}

function PasswordForm({ pending, onSubmit }: { pending: boolean; onSubmit: (current: string, next: string) => Promise<void> }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [mismatch, setMismatch] = useState(false);
  const submitted = useRef(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (submitted.current || pending) return;
    if (next !== confirmation) { setMismatch(true); return; }
    setMismatch(false); submitted.current = true;
    try { await onSubmit(current, next); } finally { setCurrent(""); setNext(""); setConfirmation(""); submitted.current = false; }
  };
  return <form className="auth-form" onSubmit={submit} aria-label="Change password">
    <label>Current password<input type="password" autoComplete="current-password" required value={current} onChange={(event) => setCurrent(event.target.value)} disabled={pending} /></label>
    <label>New password<input type="password" autoComplete="new-password" minLength={15} maxLength={256} required aria-describedby="password-help" value={next} onChange={(event) => setNext(event.target.value)} disabled={pending} /></label>
    <p id="password-help">Use 15–256 characters. Spaces are welcome. Avoid common passwords and your account details.</p>
    <label>Confirm new password<input type="password" autoComplete="new-password" required value={confirmation} onChange={(event) => { setConfirmation(event.target.value); setMismatch(false); }} disabled={pending} aria-invalid={mismatch} /></label>
    {mismatch && <p role="alert" className="field-error">The new passwords do not match.</p>}
    <button className="button button--primary" disabled={pending}>{pending ? "Changing password…" : "Change password"}</button>
  </form>;
}
