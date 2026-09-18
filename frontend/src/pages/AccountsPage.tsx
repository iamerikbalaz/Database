import { useCallback, useLayoutEffect, useRef, useState, useSyncExternalStore, type FormEvent } from "react";
import { accountsClient } from "../auth/accountsClient";
import { authClient, AuthError, type Role } from "../auth/client";
import { useSession } from "../auth/context";
import { useResource } from "../api/useResource";
import { ApiError } from "../api/errors";
import { ErrorState, LoadingState } from "../components/PageState";
import type { InternalUser } from "../api/materialDto";
import { ResourceHistoryPanel } from "../components/ResourceHistoryPanel";
import { AccountSecurityPanel } from "../components/AccountSecurityPanel";
import { NavigationLink } from "../components/NavigationLink";
import { useRecordCommand } from "../forms/useRecordCommand";
import { pendingRecordCommands } from "../forms/pendingRecordCommands";
import { PendingRecordSave } from "../forms/PendingRecordSave";
import type { Values } from "../forms/fields";

const roles: Role[] = ["PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP", "ADMIN"];
function safeError(error: unknown) {
  return error instanceof AuthError || error instanceof ApiError ? error.message : "The request failed. Check your connection and try again.";
}

export function AccountsPage({ navigate }: { navigate: (path: string) => void }) {
  const account = useSession();
  if (account?.session.user.role !== "ADMIN") return <section><h1>Access restricted</h1><p>Account administration requires an administrator.</p></section>;
  return <AccountsPageWork key={account.session.user.id} actorId={account.session.user.id} navigate={navigate} />;
}
function AccountsPageWork({ actorId, navigate }: { actorId: string; navigate: (path: string) => void }) {
  const load = useCallback(() => accountsClient.list(), []);
  const users = useResource(load);
  const pending = useSyncExternalStore(pendingRecordCommands.subscribe, () => pendingRecordCommands.get(actorId));
  const [selected, setSelected] = useState<InternalUser | null>(null);
  const [notice, setNotice] = useState("");
  const saved = (message: string) => { setSelected(null); setNotice(message); users.retry(); };
  return <section>
    <div className="page-heading"><div><p className="eyebrow">Administration</p><h1>Accounts</h1><p>Manage roles and issue temporary access. Changing roles or access signs out existing sessions.</p></div></div>
    {notice && <p role="status" className="success-notice">{notice}</p>}
    {pending && <p role="status">Resolve the pending save before changing another profile. {pending.scope.kind === "USER" ?
      <a href={`#account-save-${pending.scope.targetId ?? "new"}`}>Open pending profile save</a> :
      <NavigationLink href={pending.scope.editorPath} navigate={navigate}>Open pending form</NavigationLink>}</p>}
    <CreateAccount actorId={actorId} onSaved={saved} />
    {selected && !pending && <ResetAccess key={selected.id} user={selected} onClose={() => setSelected(null)} onSaved={() => { setSelected(null); saved("Temporary access issued. The user must change their password at next sign-in."); }} />}
    {users.error ? <ErrorState message={safeError(users.cause)} retry={users.retry} /> : !users.data ? <LoadingState label="Loading accounts…" /> :
      <div className="panel account-list"><h2>Internal accounts</h2>{users.data.map((user) => <AccountRow key={`${user.id}-${user.updatedAt}`} actorId={actorId} user={user} self={user.id === actorId} onReset={() => setSelected(user)} onSaved={saved} />)}</div>}
  </section>;
}

function RoleSelect({ value, onChange, disabled }: { value: Role; onChange: (role: Role) => void; disabled: boolean }) {
  return <select value={value} onChange={(event) => onChange(event.target.value as Role)} disabled={disabled}>{roles.map((role) => <option key={role}>{role}</option>)}</select>;
}
const emptyProfile = (): Values => ({ name: "", email: "", role: "PROCESSOR" });
function CreateAccount({ actorId, onSaved }: { actorId: string; onSaved: (message: string) => void }) {
  const [error, setError] = useState(""); const summary = useRef<HTMLParagraphElement>(null);
  const controller = useRecordCommand({ actorId,
    command: { kind: "USER", action: "CREATED", targetId: null, editorPath: "/settings/users",
      payload: (values) => ({ display_name: values.name.trim(), email: values.email.trim(), role: values.role }) },
    save: async (values, key) => {
      await accountsClient.create(values.name.trim(), values.email.trim(), values.role as Role, key);
      return { path: "/settings/users", message: "Profile created. Use Set or reset access to issue a temporary password." };
    },
    onStart: () => setError(""), onFailure: (failure) => setError(failure.message),
    onSaved: (result) => { setValues(emptyProfile()); onSaved(result.message); },
  });
  const [values, setValues] = useState(controller.ownPacket?.values ?? emptyProfile());
  const disabled = controller.busy || controller.packet !== null;
  useLayoutEffect(() => { if (error) summary.current?.focus(); }, [error]);
  return <article className="panel"><h2>Create account profile</h2><form id="account-save-new" tabIndex={-1} className="account-create" onSubmit={(event) => { event.preventDefault(); void controller.submit(values); }} aria-label="Create account" aria-busy={controller.busy}>
    <label>Display name<input required maxLength={255} value={values.name} onChange={(event) => setValues({ ...values, name: event.target.value })} disabled={disabled} /></label>
    <label>Email<input required type="email" maxLength={255} value={values.email} onChange={(event) => setValues({ ...values, email: event.target.value })} disabled={disabled} /></label>
    <label>Role<RoleSelect value={values.role as Role} onChange={(role) => setValues({ ...values, role })} disabled={disabled} /></label>
    <button className="button button--primary" disabled={disabled}>{controller.busy ? "Creating…" : "Create profile"}</button>
    {error && <p ref={summary} tabIndex={-1} role="alert" className="field-error">{error}</p>}
    <PendingRecordSave controller={controller} />
  </form></article>;
}
function AccountRow({ actorId, user, self, onReset, onSaved }: { actorId: string; user: InternalUser; self: boolean; onReset: () => void; onSaved: (message: string) => void }) {
  const [error, setError] = useState(""); const summary = useRef<HTMLParagraphElement>(null);
  const controller = useRecordCommand({ actorId,
    command: { kind: "USER", action: "UPDATED", targetId: user.id, editorPath: "/settings/users",
      payload: (values) => ({ role: values.role, is_active: values.active === "true" }) },
    save: async (values, key) => {
      await accountsClient.update(user.id, values.role as Role, values.active === "true", key);
      return { path: "/settings/users", message: "Account updated. Existing sessions have been revoked where required." };
    },
    onStart: () => setError(""), onFailure: (failure) => setError(failure.message), onSaved: (result) => onSaved(result.message),
  });
  const [values, setValues] = useState(controller.ownPacket?.values ?? { role: user.role, active: String(user.isActive) });
  const disabled = controller.busy || controller.packet !== null, role = values.role as Role, active = values.active === "true";
  useLayoutEffect(() => { if (error) summary.current?.focus(); }, [error]);
  return <div><form id={`account-save-${user.id}`} tabIndex={-1} className="account-row" onSubmit={(event) => { event.preventDefault(); if (!self) void controller.submit(values); }} aria-label={`Manage ${user.displayName}`} aria-busy={controller.busy}>
    <div><strong>{user.displayName}{self ? " (you)" : ""}</strong><div>{user.email}</div></div>
    <label>Role<RoleSelect value={role} onChange={(role) => setValues({ ...values, role })} disabled={disabled || self} /></label>
    <label className="account-active"><input type="checkbox" checked={active} onChange={(event) => setValues({ ...values, active: String(event.target.checked) })} disabled={disabled || self} />Active</label>
    <button className="button" disabled={disabled || self || (role === user.role && active === user.isActive)}>Save role and status</button>
    {!self && <button className="button" type="button" disabled={!user.isActive || disabled} onClick={onReset}>Set or reset access</button>}
    {error && <p ref={summary} tabIndex={-1} role="alert" className="field-error">{error}</p>}
    <PendingRecordSave controller={controller} />
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
