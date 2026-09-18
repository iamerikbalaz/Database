import { useCallback, useState } from "react";
import { accountSecurityClient, accountSecurityLabels } from "../api/accountSecurityClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import type { InternalUser } from "../api/materialDto";
import { ErrorState, LoadingState } from "./PageState";

function History({ userId }: { userId: string }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const load = useCallback(() => accountSecurityClient.history(userId, cursor), [userId, cursor]);
  const result = useResource(load);
  return <div className="company-history">
    <p>Successful access and password changes recorded after security auditing was enabled. Earlier actions and sign-in attempts are not reconstructed.</p>
    <p>These outcomes describe past actions. The account may have changed since then.</p>
    {result.error ? <ErrorState message="Account security history could not be verified." retry={result.retry} /> : !result.data ? <LoadingState label="Loading account security history…" /> : <>
      {!result.data.items.length && <p>No recorded security changes.</p>}
      <ol className="company-history-list">{result.data.items.map((item) => <li key={item.id}>
        <details><summary>Security change {item.version} · {accountSecurityLabels[item.action]} · {new Date(item.createdAt).toLocaleString()}</summary>
          <p className="revision-hash">{item.actorId ? `Actor: ${item.actorId}` : "Source: local host administration; no signed-in application actor."}</p>
          <p>Password change required after this action: <strong>{item.requiresChange ? "Yes" : "No"}</strong>.</p>
          <p>Credential changed at: {new Date(item.credentialChangedAt).toLocaleString()}</p>
          <p className="revision-hash">Event: {item.id}</p>
        </details>
      </li>)}</ol>
    </>}
    <div className="publication-actions"><button type="button" className="button" disabled={!result.data && !result.error} onClick={() => { if (cursor) setCursor(null); else result.retry(); }}>Latest security changes</button>
      <button type="button" className="button" disabled={!result.data?.nextCursor} onClick={() => setCursor(result.data!.nextCursor)}>Older security changes</button></div>
  </div>;
}
export function AccountSecurityPanel({ user }: { user: InternalUser }) {
  const auth = useSession(), [open, setOpen] = useState(false);
  if (auth?.session.user.role !== "ADMIN") return null;
  return <details className="panel panel--wide" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Account security history</summary>
    {open && <History key={`${auth.session.user.id}:${user.id}:${user.updatedAt}`} userId={user.id} />}
  </details>;
}
