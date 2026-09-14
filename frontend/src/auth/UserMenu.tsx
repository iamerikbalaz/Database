import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useSession } from "./sessionContext";
import { authMessage } from "./messages";

export function UserMenu() {
  const { state, store } = useSession();
  const trigger = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const summary = useRef<HTMLDivElement>(null);
  const locked = useRef(false);
  const active = useRef(true);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<{ message: string } | null>(null);
  useLayoutEffect(() => { if (failure) summary.current?.focus(); }, [failure]);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  if (state.status !== "authenticated") return null;
  const { user } = state.session;
  const mutationPending = state.pendingMutation !== null;
  // Closing the dialog does not cancel or misrepresent the server operation.
  const close = () => dialog.current?.close();
  async function signOut() {
    if (locked.current || mutationPending) return;
    locked.current = true;
    setPending(true); setFailure(null);
    try { await store.signOut(); }
    catch (error) {
      if (!active.current) return;
      if (dialog.current && !dialog.current.open) dialog.current.showModal();
      setFailure({ message: authMessage(error, "logout") });
    }
    finally { if (active.current) { locked.current = false; setPending(false); } }
  }
  return <>
    <button ref={trigger} className="button account-trigger" type="button" aria-label="User menu" aria-haspopup="dialog"
      disabled={state.pendingMutation === "change"}
      onClick={() => { dialog.current?.showModal(); if (failure) summary.current?.focus(); }}>{user.displayName}</button>
    <span className="sr-only" role="status">{pending ? "Signing out. Please wait…" : ""}</span>
    <dialog ref={dialog} className="confirm-dialog account-dialog" aria-labelledby="account-heading"
      onCancel={(event) => { event.preventDefault(); close(); }} onClose={() => trigger.current?.focus()}>
      <div className="confirm-dialog__body">
        <h2 id="account-heading">Your account</h2>
        <dl className="info-list">
          <div><dt>Name</dt><dd>{user.displayName}</dd></div>
          <div><dt>Email</dt><dd>{user.email}</dd></div>
          <div><dt>Role</dt><dd>{user.role.replaceAll("_", " ")}</dd></div>
        </dl>
        {failure && <div className="form-error" ref={summary} role="alert" tabIndex={-1}><p>{failure.message}</p></div>}
        <div className="form-actions">
          <button className="button" type="button" onClick={close}>Close</button>
          <button className="button button--primary" type="button" disabled={pending || mutationPending} onClick={() => void signOut()}>{pending ? "Signing out…" : "Sign out"}</button>
        </div>
        <p role="status">{pending ? "Signing out. Please wait…" : ""}</p>
      </div>
    </dialog>
  </>;
}
