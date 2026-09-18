import { useCallback, useState } from "react";
import { resourceHistoryClient, resourceHistoryNames, resourceHistorySchema, type ResourceKind } from "../api/resourceHistoryClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ErrorState, LoadingState } from "./PageState";

const display = (value: unknown) => value === undefined ? "Not recorded" : value === null ? "Empty" : typeof value === "boolean" ? value ? "Yes" : "No" : String(value);
function History({ kind, id }: { kind: ResourceKind; id: string }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const load = useCallback(() => resourceHistoryClient.history(kind, id, cursor), [kind, id, cursor]);
  const result = useResource(load);
  return <div className="company-history">
    <p>History covers profile creation and edits recorded after auditing was enabled. Earlier changes are not reconstructed.</p>
    <p>Saved values describe each past change. The current record may include later changes.</p>
    {kind === "MATERIAL" && <p>Workflow, review, identity, content and publication actions have separate histories in their respective sections.</p>}
    {kind === "USER" && <p>This profile history does not include sign-ins, passwords or access resets.</p>}
    {kind === "BRAND" && <p>System-managed sequence reservations are recorded separately.</p>}
    {result.error ? <ErrorState message="Record history could not be verified." retry={result.retry} /> : !result.data ? <LoadingState label="Loading record history…" /> : <>
      {!result.data.items.length && <p>No recorded profile changes.</p>}
      <ol className="company-history-list">{result.data.items.map((item) => <li key={item.id}>
        <details><summary>Change {item.version} · {item.action === "CREATED" ? "Record created" : "Record updated"} · {new Date(item.createdAt).toLocaleString()}</summary>
          <p className="revision-hash">Actor: {item.actorId}</p>
          <ul className="notion-values">{item.changed.map((field) => <li key={field}><h4>{resourceHistorySchema[kind][field][0]}</h4><dl>
            <div><dt>Before this change</dt><dd>{display(item.before?.[field])}</dd></div>
            <div><dt>After this change</dt><dd>{display(item.after[field])}</dd></div>
          </dl></li>)}</ul>
          <details><summary>History evidence</summary><p className="revision-hash">Event: {item.id}</p>
            <p className="revision-hash">Before digest: {item.beforeHash}</p><p className="revision-hash">After digest: {item.afterHash}</p></details>
        </details>
      </li>)}</ol>
    </>}
    <div className="publication-actions"><button type="button" className="button" disabled={!result.data && !result.error} onClick={() => { if (cursor) setCursor(null); else result.retry(); }}>Latest changes</button>
      <button type="button" className="button" disabled={!result.data?.nextCursor} onClick={() => setCursor(result.data!.nextCursor)}>Older changes</button></div>
  </div>;
}
export function ResourceHistoryPanel({ kind, id, updatedAt }: { kind: ResourceKind; id: string; updatedAt: string }) {
  const auth = useSession(), [open, setOpen] = useState(false);
  if (auth?.session.user.role !== "ADMIN") return null;
  return <details className="panel panel--wide" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{resourceHistoryNames[kind]} change history</summary>
    {open && <History key={`${auth.session.user.id}:${kind}:${id}:${updatedAt}`} kind={kind} id={id} />}
  </details>;
}
