import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { useSession } from "./sessionContext";
import { authMessage } from "./messages";
import { validatePasswordChange } from "./passwordValidation";

export function AuthForm({ mode }: { mode: "login" | "change" }) {
  const { state, store } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<{ message: string } | null>(null);
  const locked = useRef(false);
  const active = useRef(true);
  const first = useRef<HTMLInputElement>(null);
  const summary = useRef<HTMLDivElement>(null);
  const disabled = pending || (state.status === "authenticated" && state.pendingMutation !== null);
  useLayoutEffect(() => { first.current?.focus(); }, []);
  useLayoutEffect(() => { if (failure) summary.current?.focus(); }, [failure]);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (locked.current || disabled) return;
    const localError = mode === "login"
      ? (!email.trim() || !password ? "Enter your email and password." : null)
      : (!password ? "Enter your current password." : validatePasswordChange(newPassword, confirmation));
    if (localError) { setFailure({ message: localError }); return; }
    locked.current = true;
    setPending(true);
    setFailure(null);
    try {
      if (mode === "login") await store.signIn({ email, password });
      else await store.changePassword({ currentPassword: password, newPassword });
    } catch (error) {
      if (!active.current) return;
      setPassword(""); setNewPassword(""); setConfirmation("");
      setFailure({ message: authMessage(error, mode) });
    } finally {
      if (active.current) { locked.current = false; setPending(false); }
    }
  }
  const label = mode === "login" ? "Sign in" : "Change password";
  return <>
    <div className="page-heading"><div>
      <p className="eyebrow">Internal workspace</p>
      <h1>{label}</h1>
      <p>{mode === "login" ? "Sign in to the REAWOTE workspace." : "Change your initial password before opening the workspace."}</p>
    </div></div>
    <form className="record-form auth-form" aria-label={label} noValidate aria-busy={disabled} onSubmit={submit}>
      {failure && <div id="auth-error" ref={summary} className="form-error" role="alert" tabIndex={-1}><p>{failure.message}</p></div>}
      <fieldset disabled={disabled} aria-describedby={failure ? "auth-error" : undefined}>
        <legend className="sr-only">{label}</legend>
        {mode === "login" && <div className="form-field">
          <label htmlFor="auth-email">Email</label>
          <input ref={first} id="auth-email" name="username" type="email" autoComplete="username" value={email} required
            onChange={(event) => { setEmail(event.target.value); setFailure(null); }} />
        </div>}
        <div className="form-field">
          <label htmlFor="auth-password">{mode === "login" ? "Password" : "Current password"}</label>
          <input ref={mode === "change" ? first : undefined} id="auth-password" name="current-password" type="password" autoComplete="current-password"
            value={password} required onChange={(event) => { setPassword(event.target.value); setFailure(null); }} />
        </div>
        {mode === "change" && <>
          <div className="form-field">
            <label htmlFor="auth-new-password">New password</label>
            <input id="auth-new-password" name="new-password" type="password" autoComplete="new-password" value={newPassword} required aria-describedby="password-requirements"
              onChange={(event) => { setNewPassword(event.target.value); setFailure(null); }} />
          </div>
          <div className="form-field">
            <label htmlFor="auth-confirm-password">Confirm new password</label>
            <input id="auth-confirm-password" name="confirm-new-password" type="password" autoComplete="new-password" value={confirmation} required
              onChange={(event) => { setConfirmation(event.target.value); setFailure(null); }} />
          </div>
          <p id="password-requirements" className="auth-hint">Use 15–256 characters after Unicode normalization. Spaces and Unicode are welcome; numbers or capital letters are not required. Avoid common passwords, repeated single characters, REAWOTE and your account name. The server checks the full password policy.</p>
        </>}
      </fieldset>
      <div className="form-actions"><button className="button button--primary" type="submit" disabled={disabled}>
        {pending ? (mode === "login" ? "Signing in…" : "Changing password…") : label}
      </button></div>
      <p className="auth-progress" role="status">{pending ? (mode === "login" ? "Signing in. Please wait…" : "Changing password. Please wait…") : ""}</p>
    </form>
  </>;
}
