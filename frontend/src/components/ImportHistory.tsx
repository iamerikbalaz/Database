import { useCallback, useState } from "react";
import { importClient } from "../api/importClient";
import { useResource } from "../api/useResource";
import { ErrorState, LoadingState } from "./PageState";
import { ImportRows } from "./ImportRows";

export function ImportHistory({ navigate }: { navigate: (path: string) => void }) {
  const [cursor, setCursor] = useState<string>();
  const [selected, setSelected] = useState<string>();
  const load = useCallback(() => importClient.history(cursor), [cursor]);
  const history = useResource(load);
  const loadBatch = useCallback(() => selected ? importClient.batch(selected) : Promise.resolve(null), [selected]);
  const batch = useResource(loadBatch);
  return <article className="panel import-history"><h2>Import history</h2>
    <p>Completed batches keep their original mappings and created identities. Later material edits are shown on each material's page.</p>
    {history.error ? <ErrorState message="Import history could not be loaded." retry={history.retry} /> : !history.data ? <LoadingState label="Loading import history…" /> : <>
      {history.data.items.length === 0 ? <p>No completed imports.</p> : <ul className="import-batches">{history.data.items.map((item) => <li key={item.id}>
        <button className="button" onClick={() => setSelected(item.id)}>View {item.format} import · {item.rowCount} materials · {new Date(item.createdAt).toLocaleString()}</button>
        <span>{item.reason}</span></li>)}</ul>}
      <div className="import-pagination"><button className="button" disabled={!cursor} onClick={() => setCursor(undefined)}>Newest imports</button>
        <button className="button" disabled={!history.data.next} onClick={() => setCursor(history.data?.next ?? undefined)}>Older imports</button>
        <button className="button" onClick={history.retry}>Reload import history</button></div>
    </>}
    {selected && <section aria-label="Import batch details"><h3>Saved import batch</h3>
      <button className="button" onClick={() => setSelected(undefined)}>Close batch details</button>
      {batch.error ? <ErrorState message="The saved batch could not be loaded." retry={batch.retry} /> : !batch.data ? <LoadingState label="Loading saved batch…" /> : <>
        <p>{batch.data.reason}</p><p>Batch: <code>{batch.data.id}</code></p>
        <ImportRows key={batch.data.id} data={batch.data} navigate={navigate} />
      </>}
    </section>}
  </article>;
}
