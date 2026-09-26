import { useCallback, useState } from "react";
import { NavigationLink } from "../components/NavigationLink";
import { useResource } from "../api/useResource";
import type { ApiClient } from "../api/client";
import type { Project } from "../types";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { Icon } from "../components/Icon";
import { useSession } from "../auth/context";
import { EditableResourceTable, type ResourceColumn } from "../components/EditableResourceTable";
import { NasFolderReference } from "../components/NasFolderReference";
import { statusToDto } from "../api/dto";
import type { ProjectPatchDto } from "../api/writeDto";

export function ProjectsPage({ client, navigate }: { client: ApiClient; navigate: (path: string) => void }) {
  const request = useCallback(() => Promise.all([client.getProjects(), client.getCompanies()]), [client]);
  const { data, error, retry: load } = useResource(request);
  const [query, setQuery] = useState(""), [status, setStatus] = useState(""), [company, setCompany] = useState(""), [linked, setLinked] = useState("");
  const [busy, setBusy] = useState(false);
  const role = useSession()?.session.user.role, canEdit = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const companies = data?.[1] ?? [], names = new Map(companies.map(item => [item.id, item.name]));
  const items = (data?.[0] ?? []).filter(row => (!status || row.status === status) && (!company || row.companyId === company)
    && (!linked || Boolean(row.folderPath) === (linked === "yes")) && `${row.number} ${row.name} ${row.description ?? ""} ${row.folderPath ?? ""} ${names.get(row.companyId) ?? ""}`.toLowerCase().includes(query.toLowerCase()));
  const columns: ResourceColumn<Project>[] = [
    { key: "name", label: "Project", value: row => row.name, editable: true, render: row => <><span className="project-number">{row.number}</span><NavigationLink className="table-link" href={`/projects/${row.id}`} navigate={navigate}>{row.name}</NavigationLink></> },
    { key: "project_number", label: "Number", value: row => row.number },
    { key: "company_id", label: "Company", value: row => row.companyId, editable: true, bulk: true, options: companies.map(row => ({ value: row.id, label: row.name })) },
    { key: "status", label: "Status", value: row => statusToDto(row.status), editable: true, bulk: true, options: [{ value: "NOT_STARTED", label: "Not started" }, { value: "IN_PROGRESS", label: "In progress" }, { value: "DONE", label: "Done" }] },
    { key: "due_date", label: "Deadline", type: "date", value: row => row.dueDate, editable: true, bulk: true },
    { key: "notes", label: "Note", type: "textarea", value: row => row.description, editable: true, bulk: true },
    { key: "folder_path", label: "NAS folder", value: row => row.folderPath ?? null, render: row => <NasFolderReference path={row.folderPath} /> },
    { key: "created_at", label: "Created", value: row => row.createdAt }, { key: "updated_at", label: "Updated", value: row => row.updatedAt },
  ];
  return <section><div className="page-heading"><div><p className="eyebrow">Production</p><h1>Projects</h1><p>Project records, client work and links to the original NAS folders.</p></div><NavigationLink className="button button--primary" href="/projects/new" navigate={navigate}><Icon name="plus" size={18} />Add project</NavigationLink></div>
    <fieldset className="toolbar resource-filters" disabled={busy}><label className="search"><span className="sr-only">Search projects</span><Icon name="search" size={18} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search projects and notes…" /></label>
      <label>Status<select aria-label="Filter project status" value={status} onChange={e => setStatus(e.target.value)}><option value="">All statuses</option><option value="not_started">Not started</option><option value="in_progress">In progress</option><option value="done">Done</option></select></label>
      <label>Company<select aria-label="Filter project company" value={company} onChange={e => setCompany(e.target.value)}><option value="">All companies</option>{companies.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
      <label>NAS folder<select aria-label="Filter linked folders" value={linked} onChange={e => setLinked(e.target.value)}><option value="">All projects</option><option value="yes">Linked</option><option value="no">Not linked</option></select></label><span className="result-count">{items.length} projects</span></fieldset>
    {error ? <ErrorState message="Projects are temporarily unavailable. Check your connection and try again." retry={load} /> : !data ? <LoadingState label="Loading projects…" /> : <>
      {!items.length && <EmptyState title={data[0].length ? "No matching projects" : "No projects yet"} description="Add projects or adjust the current filters." />}
      <EditableResourceTable rows={items} columns={columns} label={row => `${row.number} · ${row.name}`} canEdit={canEdit} storageKey="projects.columns.v1" refresh={load} onBusyChange={setBusy}
        save={(row, field, value, key) => client.updateProject(row.id, { [field]: value, expected_updated_at: row.updatedAt } as ProjectPatchDto, key)} />
    </>}</section>;
}
