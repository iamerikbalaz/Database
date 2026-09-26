import { useCallback, useState } from "react";
import { NavigationLink } from "../components/NavigationLink";
import { useResource } from "../api/useResource";
import type { ApiClient } from "../api/client";
import type { Company } from "../types";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { Icon } from "../components/Icon";
import { useSession } from "../auth/context";
import { EditableResourceTable, type ResourceColumn } from "../components/EditableResourceTable";
import type { CompanyPatchDto } from "../api/writeDto";

export function CompaniesPage({ client, navigate }: { client: ApiClient; navigate: (path: string) => void }) {
  const [query, setQuery] = useState(""), [status, setStatus] = useState(""), [country, setCountry] = useState(""), [busy, setBusy] = useState(false);
  const request = useCallback(() => client.getCompanies(), [client]);
  const { data, error, retry: load } = useResource<Company[]>(request);
  const role = useSession()?.session.user.role, canEdit = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const items = data ?? [], countries = [...new Set(items.map(row => row.country).filter((value): value is string => Boolean(value)))].sort();
  const filtered = items.filter(row => (!status || row.status === status) && (!country || row.country === country) && `${row.name} ${row.officialName ?? ""} ${row.country ?? ""} ${row.address ?? ""} ${row.websiteUrl ?? ""} ${row.vatId ?? ""}`.toLowerCase().includes(query.toLowerCase()));
  const columns: ResourceColumn<Company>[] = [
    { key: "name", label: "Company", value: row => row.name, editable: true, render: row => <NavigationLink className="table-link" href={`/companies/${row.id}`} navigate={navigate}>{row.name}</NavigationLink> },
    { key: "legal_name", label: "Legal name", value: row => row.officialName, editable: true },
    { key: "country", label: "Country", value: row => row.country, editable: true, bulk: true },
    { key: "address", label: "Address", type: "textarea", value: row => row.address, editable: true },
    { key: "website", label: "Website", value: row => row.websiteUrl, editable: true },
    { key: "vat_id", label: "VAT ID", value: row => row.vatId, editable: true },
    { key: "is_active", label: "Active", type: "boolean", value: row => row.status === "active", editable: true, bulk: true },
    { key: "created_at", label: "Created", value: row => row.createdAt }, { key: "updated_at", label: "Updated", value: row => row.updatedAt },
  ];
  return <section><div className="page-heading"><div><p className="eyebrow">Directory</p><h1>Companies</h1><p>Manage clients, their published brands and related projects.</p></div><NavigationLink className="button button--primary" href="/companies/new" navigate={navigate}><Icon name="plus" size={18} />Add company</NavigationLink></div>
    <fieldset className="toolbar resource-filters" disabled={busy}><label className="search"><span className="sr-only">Search companies</span><Icon name="search" size={18} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search companies…" /></label>
      <label>Active<select aria-label="Filter company activity" value={status} onChange={e => setStatus(e.target.value)}><option value="">All companies</option><option value="active">Active</option><option value="inactive">Inactive</option></select></label>
      <label>Country<select aria-label="Filter company country" value={country} onChange={e => setCountry(e.target.value)}><option value="">All countries</option>{countries.map(value => <option key={value}>{value}</option>)}</select></label><span className="result-count">{filtered.length} {filtered.length === 1 ? "company" : "companies"}</span></fieldset>
    {error ? <ErrorState message="Companies are temporarily unavailable. Check your connection and try again." retry={load} /> : !data ? <LoadingState label="Loading companies…" /> : <>
      {!filtered.length && <EmptyState title={items.length ? "No matching companies" : "No companies yet"} description={items.length ? "Try a different search term." : "Add your first company to start building the directory."} />}
      <EditableResourceTable rows={filtered} columns={columns} label={row => row.name} canEdit={canEdit} storageKey="companies.columns.v1" refresh={load} onBusyChange={setBusy}
        save={(row, field, value, key) => client.updateCompany(row.id, { [field]: value, expected_updated_at: row.updatedAt } as CompanyPatchDto, key)} />
    </>}</section>;
}
