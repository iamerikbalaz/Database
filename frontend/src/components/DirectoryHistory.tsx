import { useCallback, useState } from "react";
import { directoryClient } from "../api/directoryClient";
import { useResource } from "../api/useResource";
import { ErrorState, LoadingState } from "./PageState";
function display(value: unknown): string { return value === null || value === undefined ? "Empty" : typeof value === "object" ? JSON.stringify(value) : String(value); }
function Events({ kind, id }: { kind: "customer" | "order"; id: string }) {
  const result = useResource(useCallback(() => directoryClient.history(kind, id), [kind, id]));
  return result.error ? <ErrorState message="Record history could not be loaded." retry={result.retry} /> : !result.data ? <LoadingState label="Loading history…" /> : <>
    {!result.data.length && <p>No recorded changes.</p>}<ol className="company-history-list">{result.data.map(event => <li key={event.id}><details><summary>{event.action.toLowerCase()} · {new Date(event.createdAt).toLocaleString()} · {event.actorName ?? event.actorId}</summary>
      {event.source === "LEGACY_COMPANY" && <p className="muted">Recorded on the former company profile.</p>}<dl className="info-list">{Object.keys(event.after).filter(key => JSON.stringify(event.before?.[key]) !== JSON.stringify(event.after[key])).map(key => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{display(event.before?.[key])} → {display(event.after[key])}</dd></div>)}</dl>
    </details></li>)}</ol><p className="muted">Latest 200 recorded changes.</p>
  </>;
}
export function DirectoryHistory({ kind, id, updatedAt }: { kind: "customer" | "order"; id: string; updatedAt: string }) {
  const [open, setOpen] = useState(false);
  return <details className="panel" onToggle={event => setOpen(event.currentTarget.open)}><summary>{kind === "customer" ? "Customer" : "Order"} change history</summary>{open && <Events key={updatedAt} kind={kind} id={id} />}</details>;
}
