import { useCallback, useRef, useState, type FormEvent } from "react";
import { accountsClient } from "../auth/accountsClient";
import { authClient, AuthError, type Role } from "../auth/client";
import { useSession } from "../auth/context";
import { useResource } from "../api/useResource";
import { ApiError } from "../api/errors";
import { ErrorState, LoadingState } from "../components/PageState";
import type { InternalUser } from "../api/materialDto";
import { ResourceHistoryPanel } from "../components/ResourceHistoryPanel";
import { AccountSecurityPanel } from "../components/AccountSecurityPanel";

const roles: Role[] = ["PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP", "ADMIN"];
function safeError(error: unknown) {
  return error instanceof AuthError || error instanceof ApiError ? error.message : "The request failed. Check your connection and try again.";
}

export function AccountsPage() {
  const account = useSession();
  const load = useCallback(() => accountsClient.list(), []);
  const users = useResource(load);
  const [selected, setSelected] = useState<InternalUser | null>(null);
  const [notice, setNotice] = useState("");
  const saved = (message: string) => { setNotice(message); users.retry(); };
  if (account?.session.user.role !== "ADMIN") return <section><h1>Access restricted</h1><p>Account administration requires an administrator.</p></section>;
  return <section>
    <div className="page-heading"><div><p className="eyebrow">Administration</p><h1>Accounts</h1><p>Manage roles and issue temporary access. Changing roles or access signs out existing sessions.</p></div></div>
    {notice && <p role="status" className="success-notice">{notice}</p>}
    <CreateAccount onSaved={() => saved("Profile created. Use Set or reset access to issue a temporary password.")} />
    {selected && <ResetAccess key={selected.id} user={selected} onClose={() => setSelected(null)} onSaved={() => { setSelected(null); saved("Temporary access issued. The user must change their password at next sign-in."); }} />}
    {users.error ? <ErrorState message={safeError(users.cause)} retry={users.retry} /> : !users.data ? <LoadingState label="Loading accounts…" /> :
      <div className="panel account-list"><h2>Internal accounts</h2>{users.data.map((user) => <AccountRow key={`${user.id}-${user.updatedAt}`} user={user} self={user.id === account.session.user.id} onReset={() => setSelected(user)} onSaved={() => saved("Account updated. Existing sessions have been revoked where required.")} />)}</div>}
  </section>;
}

function RoleSelect({ value, onChange, disabled }: { value: Role; onChange: (role: Role) => void; disabled: boolean }) {
  return <select value={value} onChange={(event) => onChange(event.target.value as Role)} disabled={disabled}>{roles.map((role) => <option key={role}>{role}</option>)}</select>;
}
function CreateAccount({ onSaved }: { onSaved: () => void }) {
  const [name, setName] = useState(""); const [email, setEmail] = useState(""); const [role, setRole] = useState<Role>("PROCESSOR");
  const [pending, setPending] = useState(false); const [error, setError] = useState(""); const sending = useRef(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (sending.current) return;
    sending.current = true; setPending(true); setError("");
    try { await accountsClient.create(name.trim(), email.trim(), role); setName(""); setEmail(""); setRole("PROCESSOR"); onSaved(); }
    catch (cause) { setError(safeError(cause)); } finally { sending.current = false; setPending(false); }
  };
  return <article className="panel"><h2>Create account profile</h2><form className="account-create" onSubmit={submit} aria-label="Create account">
    <label>Display name<input required maxLength={255} value={name} onChange={(event) => setName(event.target.value)} disabled={pending} /></label>
    <label>Email<input required type="email" maxLength={255} value={email} onChange={(event) => setEmail(event.target.value)} disabled={pending} /></label>
    <label>Role<RoleSelect value={role} onChange={setRole} disabled={pending} /></label>
    <button className="button button--primary" disabled={pending}>{pending ? "Creating…" : "Create profile"}</button>
    {error && <p role="alert" className="field-error">{error}</p>}
  </form></article>;
}
function AccountRow({ user, self, onReset, onSaved }: { user: InternalUser; self: boolean; onReset: () => void; onSaved: () => void }) {
  const [role, setRole] = useState<Role>(user.role); const [active, setActive] = useState(user.isActive);
  const [pending, setPending] = useState(false); const [error, setError] = useState(""); const sending = useRef(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (sending.current) return;
    sending.current = true; setPending(true); setError("");
    try { await accountsClient.update(user.id, role, active); onSaved(); }
    catch (cause) { setError(safeError(cause)); } finally { sending.current = false; setPending(false); }
  };
  return <div><form className="account-row" onSubmit={submit} aria-label={`Manage ${user.displayName}`}>
    <div><strong>{user.displayName}{self ? " (you)" : ""}</strong><div>{user.email}</div></div>
    <label>Role<RoleSelect value={role} onChange={setRole} disabled={pending || self} /></label>
    <label className="account-active"><input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} disabled={pending || self} />Active</label>
    <button className="button" disabled={pending || self || (role === user.role && active === user.isActive)}>Save role and status</button>
    {!self && <button className="button" type="button" disabled={!user.isActive || pending} onClick={onReset}>Set or reset access</button>}
    {error && <p role="alert" className="field-error">{error}</p>}
  </form><ResourceHistoryPanel kind="USER" id={user.id} updatedAt={user.updatedAt} /><AccountSecurityPanel user={user} /></div>;
}
function ResetAccess({ user, onClose, onSaved }: { user: InternalUser; onClose: () => void; onSaved: () => void }) {
  const [current, setCurrent] = useState(""); const [next, setNext] = useState(""); const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false); const [error, setError] = useState(""); const sending = useRef(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (sending.current) return;
    if (next !== confirmation) { setError("The temporary passwords do not match."); return; }
    sending.current = true; setPending(true); setError("");
    try { await authClient.provisionAccess(user.id, current, next); onSaved(); }
    catch (cause) { setError(safeError(cause)); }
    finally { setCurrent(""); setNext(""); setConfirmation(""); sending.current = false; setPending(false); }
  };
  return <article className="panel"><h2>Set access for {user.displayName}</h2><p>This signs out all sessions and requires a password change. Share the temporary password through your approved private channel.</p>
    <form className="auth-form" aria-label="Set temporary access" onSubmit={submit}>
      <label>Your current password<input type="password" autoComplete="current-password" required value={current} onChange={(event) => setCurrent(event.target.value)} disabled={pending} /></label>
      <label>Temporary password<input type="password" autoComplete="new-password" minLength={15} maxLength={256} required value={next} onChange={(event) => setNext(event.target.value)} disabled={pending} /></label>
      <label>Confirm temporary password<input type="password" autoComplete="new-password" required value={confirmation} onChange={(event) => setConfirmation(event.target.value)} disabled={pending} /></label>
      {error && <p role="alert" className="field-error">{error}</p>}
      <div className="auth-actions"><button className="button button--primary" disabled={pending}>Issue temporary access</button><button type="button" className="button" disabled={pending} onClick={onClose}>Cancel</button></div>
    </form>
  </article>;
}
