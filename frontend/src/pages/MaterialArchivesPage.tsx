import { useCallback, useState } from "react";
import { materialArchiveClient as api } from "../api/materialArchiveClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ErrorState, LoadingState } from "../components/PageState";
import { NavigationLink } from "../components/NavigationLink";
import { MaterialLifecyclePanel } from "../components/MaterialLifecyclePanel";
import { PackagingJobsPanel } from "../components/PackagingJobsPanel";
import { ResourceHistoryPanel } from "../components/ResourceHistoryPanel";

function ArchiveList({ navigate }: { navigate: (path: string) => void }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const load = useCallback(() => api.list(cursor), [cursor]), result = useResource(load);
  return <section><h1>Archived materials</h1><p>Records removed from active work. Original files and saved history are preserved.</p>
    {result.error ? <ErrorState message="Archived materials could not be loaded." retry={result.retry} /> : !result.data ? <LoadingState label="Loading archived materials…" /> : <>
      {!result.data.items.length && <p>No archived materials on this page.</p>}
      <ul className="company-history-list">{result.data.items.map((item) => <li key={item.id}>
        <NavigationLink href={`/material-archives/${item.id}`} navigate={navigate}>{item.name}</NavigationLink>
        <p className="revision-hash">{item.technicalIdentity}</p><p>Archived {new Date(item.changedAt!).toLocaleString()}</p>
      </li>)}</ul>
      <div className="publication-actions"><button className="button" onClick={() => { if (cursor) setCursor(null); else result.retry(); }}>First archive page</button>
        <button className="button" disabled={!result.data.nextCursor} onClick={() => setCursor(result.data!.nextCursor)}>More archived materials</button></div>
    </>}
  </section>;
}
function ArchiveRecord({ id, navigate }: { id: string; navigate: (path: string) => void }) {
  const load = useCallback(() => api.detail(id), [id]), result = useResource(load);
  if (result.error) return <ErrorState message="This material's lifecycle state could not be verified." retry={result.retry} />;
  if (!result.data) return <LoadingState label="Loading material lifecycle…" />;
  const item = result.data;
  return <section><div className="page-heading"><div><h1>{item.name}</h1><p>{item.isArchived ? "Archived material" : "Active material"} · {item.technicalIdentity}</p></div>
    <button className="button" onClick={result.retry}>Refresh material state</button></div>
    <div className="publication-actions"><NavigationLink href="/material-archives" navigate={navigate}>All archived materials</NavigationLink>
      {!item.isArchived && <NavigationLink href={`/materials/${id}`} navigate={navigate}>Open active material</NavigationLink>}</div>
    <dl className="info-list"><dt>Reserved number</dt><dd>{item.sequenceNumber}</dd>
      <dt>Source folder</dt><dd>{item.folderPath ?? "No linked folder"}</dd>
      <dt>Last lifecycle change</dt><dd>{item.changedAt ? new Date(item.changedAt).toLocaleString() : "No archive or restore recorded"}</dd></dl>
    <MaterialLifecyclePanel materialId={id} archived={item.isArchived} navigate={navigate} onApplied={() => result.retry()} />
    <PackagingJobsPanel materialId={id} />
    <ResourceHistoryPanel kind="MATERIAL" id={id} updatedAt={item.changedAt ?? ""} />
  </section>;
}
export function MaterialArchivesPage({ id, navigate }: { id?: string; navigate: (path: string) => void }) {
  const auth = useSession();
  if (auth?.session.user.role !== "ADMIN") return <section><h1>Access restricted</h1><p>Only an administrator can manage archived materials.</p></section>;
  return id ? <ArchiveRecord key={`${auth.session.user.id}:${id}`} id={id} navigate={navigate} />
    : <ArchiveList key={auth.session.user.id} navigate={navigate} />;
}
