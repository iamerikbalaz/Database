import { useCallback, useState } from "react";
import type { ApiClient } from "../api/client";
import { materialLoadError } from "../api/materialClient";
import { statusLabel, type Material } from "../api/materialDto";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { NavigationLink } from "../components/NavigationLink";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";

type Props = { client: ApiClient; navigate: (path: string) => void };
const pageSize = 10;
const views = [
  { id: "all", label: "All materials", includes: () => true },
  { id: "progress", label: "In progress", includes: (m: Material) => m.workflowStatus === "IN_PROGRESS" },
  { id: "done", label: "Done", includes: (m: Material) => m.workflowStatus === "DONE" },
  { id: "findings", label: "Validation findings", includes: (m: Material) => ["WARNING", "ERROR", "METADATA_MISSING"].includes(m.validationStatus) },
] as const;
type View = typeof views[number]["id"];

export function DashboardPage(props: Props) {
  const user = useSession()?.session.user;
  // Reset data, filters and pending callbacks when the account or role changes.
  return <Dashboard key={`${user?.id ?? "isolated"}:${user?.role ?? "none"}`} {...props} />;
}

function Dashboard({ client, navigate }: Props) {
  const user = useSession()?.session.user;
  const load = useCallback(() => client.getMaterials(), [client]);
  const result = useResource(load);
  const [view, setView] = useState<View>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const selectedView = views.find((item) => item.id === view)!;
  const query = search.trim().toLocaleLowerCase();
  const materials = result.data ?? [];
  const filtered = materials.filter((m) => selectedView.includes(m) &&
    (!query || m.materialName.toLocaleLowerCase().includes(query) || m.technicalIdentity.toLocaleLowerCase().includes(query)))
    .sort((a, b) => a.technicalIdentity.localeCompare(b.technicalIdentity, "en") || a.id.localeCompare(b.id));
  const lastPage = Math.max(0, Math.ceil(filtered.length / pageSize) - 1);
  const currentPage = Math.min(page, lastPage);
  const start = currentPage * pageSize;
  const refresh = () => { setPage(0); result.retry(); };

  return <section aria-labelledby="dashboard-title">
    <div className="page-heading"><div>
      <p className="eyebrow">Production overview</p><h1 id="dashboard-title">Dashboard</h1>
      <p>{user?.role === "PROCESSOR" ? "Your assigned materials" : "Active material records"}. Open a material to continue its workflow.</p>
    </div><button className="button" disabled={!result.data && !result.error} onClick={refresh}>Refresh overview</button></div>
    <nav className="dashboard-shortcuts" aria-label="Workspace shortcuts">
      <NavigationLink className="button" href="/materials" navigate={navigate}>Browse materials</NavigationLink>
      <NavigationLink className="button" href="/materials/new" navigate={navigate}>Add material</NavigationLink>
      <NavigationLink className="button" href="/publication" navigate={navigate}>Prepare publication</NavigationLink>
    </nav>
    {result.error ? <ErrorState message={materialLoadError(result.cause)} retry={refresh} />
      : !result.data ? <LoadingState label="Loading overview…" />
      : <>
        <div className="dashboard-counts" role="group" aria-label="Material status filters">
          {views.map((item) => <button key={item.id} className="dashboard-count" aria-pressed={view === item.id}
            onClick={() => { setView(item.id); setPage(0); }}>
            <span>{item.label}</span><strong>{materials.filter(item.includes).length}</strong>
          </button>)}
        </div>
        <p className="dashboard-note">Counts cover the active records you can access, as of the last refresh. Validation findings include warnings, errors and missing metadata. Done does not mean approved or published.</p>
        {!result.data.length ? <EmptyState title="No materials available" description={user?.role === "PROCESSOR"
          ? "No active materials are assigned to you. Ask your production lead about your next assignment."
          : "Add a material or browse projects to start production."} />
          : <section className="panel" aria-labelledby="dashboard-materials-title">
            <h2 id="dashboard-materials-title">{selectedView.label}</h2>
            <label className="form-field">Find a material<input type="search" value={search}
              onChange={(event) => { setSearch(event.target.value); setPage(0); }} /></label>
            {!filtered.length ? <EmptyState title="No matching materials" description="Choose another status or clear your search." /> : <>
              <p role="status">Showing {start + 1}–{Math.min(start + pageSize, filtered.length)} of {filtered.length}</p>
              <ul className="dashboard-materials" aria-label="Overview materials">
                {filtered.slice(start, start + pageSize).map((m) => <li key={m.id}>
                  <div><NavigationLink className="table-link" href={`/materials/${m.id}`} navigate={navigate}>{m.technicalIdentity}</NavigationLink>
                    <p>{m.materialName}</p></div>
                  <dl><div><dt>Workflow</dt><dd>{statusLabel(m.workflowStatus)}</dd></div>
                    <div><dt>Validation</dt><dd>{statusLabel(m.validationStatus)}</dd></div>
                    <div><dt>Publication</dt><dd>{statusLabel(m.publicationStatus)}</dd></div></dl>
                </li>)}
              </ul>
              {lastPage > 0 && <nav className="dashboard-pagination" aria-label="Overview pages">
                <button className="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous materials</button>
                <span>Page {currentPage + 1} of {lastPage + 1}</span>
                <button className="button" disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>Next materials</button>
              </nav>}
            </>}
          </section>}
      </>}
  </section>;
}
