import { useCallback, useState } from "react";
import { request } from "../api/client";
import { nullable, record, string, uuid } from "../api/dto";
import { useResource } from "../api/useResource";
import { ErrorState, LoadingState } from "./PageState";

function history(input: unknown, id: string) {
  const data = record(input);
  if (uuid(data.material_id) !== id || !Array.isArray(data.items) || data.items.length > 100) throw new Error("Invalid material history");
  const items = data.items.map(input => {
    const item = record(input), author = item.author === null ? null : record(item.author);
    if (!Array.isArray(item.changes) || item.changes.length > 1000) throw new Error("Invalid history changes");
    return { id: string(item.id), source: string(item.source), action: string(item.action), createdAt: string(item.created_at),
      author: author ? { id: uuid(author.id), name: string(author.display_name) } : null,
      summary: string(item.summary), reason: nullable(item.reason), changes: item.changes.map(input => {
        const change = record(input); return { field: string(change.field), label: string(change.label), before: change.before, after: change.after };
      }) };
  });
  if (new Set(items.map(item => item.id)).size !== items.length) throw new Error("Duplicate history events");
  return { items, nextCursor: nullable(data.next_cursor) };
}
const display = (value: unknown) => value === null || value === undefined ? "Empty" : typeof value === "boolean" ? value ? "Yes" : "No" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
function MaterialHistory({ id }: { id: string }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const load = useCallback(async () => history(await request(`/materials/${uuid(id)}/activity?limit=30${cursor ? `&after=${encodeURIComponent(cursor)}` : ""}`), id), [id, cursor]);
  const result = useResource(load);
  return <>
    {result.error ? <ErrorState message="Material history could not be loaded." retry={result.retry} /> : !result.data ? <LoadingState label="Loading material history…" /> : <>
      {!result.data.items.length && <p>No recorded changes.</p>}
      <ol className="company-history-list">{result.data.items.map(item => <li key={item.id}><details>
        <summary>{item.summary} · {new Date(item.createdAt).toLocaleString()}</summary>
        <p><strong>Author:</strong> {item.author?.name || "Author not recorded"}</p>
        {item.reason && <p>{item.reason}</p>}
        <ul className="notion-values">{item.changes.map((change, index) => <li key={`${change.field}:${index}`}><h4>{change.label}</h4><dl>
          <div><dt>Before</dt><dd><pre>{display(change.before)}</pre></dd></div><div><dt>After</dt><dd><pre>{display(change.after)}</pre></dd></div>
        </dl></li>)}</ul>
      </details></li>)}</ol>
    </>}
    <div className="form-actions"><button type="button" className="button" onClick={() => cursor ? setCursor(null) : result.retry()}>Latest changes</button>
      <button type="button" className="button" disabled={!result.data?.nextCursor} onClick={() => setCursor(result.data!.nextCursor)}>Older changes</button></div>
  </>;
}
export function MaterialHistoryPanel({ id, updatedAt, refreshRevision = 0 }: { id: string; updatedAt: string; refreshRevision?: number }) {
  const [open, setOpen] = useState(false);
  return <details className="panel panel--wide material-history-panel" onToggle={event => { if (event.target === event.currentTarget) setOpen(event.currentTarget.open); }}>
    <summary><h2>Material record change history</h2></summary>{open && <MaterialHistory key={`${id}:${updatedAt}:${refreshRevision}`} id={id} />}
  </details>;
}
