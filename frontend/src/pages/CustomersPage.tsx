import { useCallback, useEffect, useState } from "react";
import { directoryClient, customerStatuses, type Customer } from "../api/directoryClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { EditableResourceTable, type ResourceColumn } from "../components/EditableResourceTable";
import { DirectorySync } from "../components/DirectorySync";
import { NavigationLink } from "../components/NavigationLink";
import { ErrorState, LoadingState } from "../components/PageState";
import { Icon } from "../components/Icon";
import { categoryLabel } from "../data/materialCategories";

const workspaceQuery = "(min-width: 900px) and (min-height: 700px)";
function useWorkspaceSize() {
  const [available, setAvailable] = useState(() => typeof window.matchMedia !== "function" || window.matchMedia(workspaceQuery).matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(workspaceQuery), update = () => setAvailable(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return available;
}

export function CustomersPage({ navigate }: { navigate: (path: string) => void }) {
  const resource = useResource(useCallback(() => directoryClient.customers(), []));
  const [search, setSearch] = useState(""), [status, setStatus] = useState(""), [category, setCategory] = useState(""), [busy, setBusy] = useState(false);
  const [dates, setDates] = useState({ createdFrom: "", createdTo: "", updatedFrom: "", updatedTo: "" });
  const [preferWorkspace, setPreferWorkspace] = useState(() => { try { return localStorage.getItem("customers.workspace-view") !== "page"; } catch { return true; } });
  const available = useWorkspaceSize(), compact = preferWorkspace && available;
  const dateCount = Object.values(dates).filter(Boolean).length;
  const role = useSession()?.session.user.role, canEdit = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const all = resource.data ?? [], categories = [...new Set(all.flatMap(item => item.mainCategoryCodes))].sort();
  const filtered = all.filter(item => (!status || item.status === status) && (!category || item.mainCategoryCodes.includes(category)) &&
    (!dates.createdFrom || item.createdAt.slice(0, 10) >= dates.createdFrom) && (!dates.createdTo || item.createdAt.slice(0, 10) <= dates.createdTo) &&
    (!dates.updatedFrom || item.updatedAt.slice(0, 10) >= dates.updatedFrom) && (!dates.updatedTo || item.updatedAt.slice(0, 10) <= dates.updatedTo) &&
    [item.name, item.legalName, item.brandIdentifier, item.address, item.shippingAddress, item.vatId, item.website, item.description, item.notes].some(value => value?.toLowerCase().includes(search.toLowerCase())));
  const columns: ResourceColumn<Customer>[] = [
    { key: "logo", label: "Logo", value: () => null, render: item => item.hasLogo ? <img className="customer-logo customer-logo--thumbnail" src={directoryClient.logoUrl(item)} alt={`${item.name} logo`} loading="lazy" /> : <span className="company-monogram">{item.name.slice(0, 2).toUpperCase()}</span> },
    { key: "name", label: "Customer", value: item => item.name, render: item => <NavigationLink className="table-link" href={`/customers/${item.id}`} navigate={navigate}>{item.name}</NavigationLink> },
    { key: "brand_identifier", label: "Brand identifier", value: item => item.brandIdentifier, editable: true },
    { key: "status", label: "Status", value: item => item.status, editable: true, bulk: true, options: customerStatuses.map(value => ({ value, label: value })) },
    { key: "categories", label: "Main categories", value: item => item.mainCategoryCodes.map(categoryLabel).join(", ") },
    { key: "website", label: "Website", value: item => item.website, editable: true },
    { key: "address", label: "Address", value: item => item.address, type: "textarea", editable: true },
    { key: "shipping_address", label: "Shipping address", value: item => item.shippingAddress, type: "textarea", editable: true },
    { key: "legal_name", label: "Legal name", value: item => item.legalName, editable: true },
    { key: "vat_id", label: "VAT ID", value: item => item.vatId, editable: true },
    { key: "description", label: "Company description", value: item => item.description, type: "textarea", editable: true },
    { key: "notes", label: "Notes", value: item => item.notes, type: "textarea", editable: true, bulk: true },
    { key: "is_active", label: "Active", value: item => item.isActive, type: "boolean", editable: true, bulk: true },
    { key: "sync", label: "Notion", value: item => item.sync?.state ?? null, render: item => <DirectorySync sync={item.sync} notionPageId={item.notionPageId} /> },
    { key: "created", label: "Created", value: item => item.createdAt }, { key: "updated", label: "Updated", value: item => item.updatedAt },
  ];
  return <section className={`customers-page${compact ? " customers-page--workspace" : ""}`}><div className="page-heading"><div><p className="eyebrow">Directory</p><h1>Customers</h1><p className="customers-description">Customer profiles, materials and orders.</p></div><div className="customers-heading-actions"><label className="checkbox-label"><input type="checkbox" checked={preferWorkspace} disabled={busy} onChange={event => { setPreferWorkspace(event.target.checked); try { localStorage.setItem("customers.workspace-view", event.target.checked ? "workspace" : "page"); } catch { /* The fallback also works without browser storage. */ } }} />Fixed workspace</label>{canEdit && <NavigationLink className="button button--primary" href="/customers/new" navigate={navigate}><Icon name="plus" size={18} />Add customer</NavigationLink>}</div></div>
    {preferWorkspace && !available && <p className="customers-size-notice">Page view is used in smaller windows to keep all controls accessible.</p>}
    <fieldset className="material-filters database-filters" disabled={busy}><legend className="sr-only">Filter customers</legend>
      <label className="customers-search">Search customers<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Name, identifier or notes" /></label>
      <label>Status<select value={status} onChange={event => setStatus(event.target.value)}><option value="">All</option>{customerStatuses.map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Main category<select value={category} onChange={event => setCategory(event.target.value)}><option value="">All categories</option>{categories.map(value => <option key={value} value={value}>{categoryLabel(value)}</option>)}</select></label>
      <details className="customers-date-filters" open={compact ? undefined : true} onKeyDown={event => { if (event.key === "Escape" && compact) { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); } }}><summary>Dates{dateCount > 0 ? ` (${dateCount})` : ""}</summary><div className="customers-date-options">
        {([["createdFrom", "Created from"], ["createdTo", "Created to"], ["updatedFrom", "Updated from"], ["updatedTo", "Updated to"]] as const).map(([key, label]) => <label key={key}>{label}<input type="date" value={dates[key]} onChange={event => setDates(previous => ({ ...previous, [key]: event.target.value }))} /></label>)}
      </div></details>
      <button className="button" onClick={() => { setSearch(""); setStatus(""); setCategory(""); setDates({ createdFrom: "", createdTo: "", updatedFrom: "", updatedTo: "" }); }}>Clear filters</button>
    </fieldset><p className="result-count">{filtered.length} customers</p>
    {resource.error ? <ErrorState message="Customers could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading customers…" /> : <>
      {!filtered.length && <p>No customers match these filters.</p>}
      <EditableResourceTable rows={filtered} columns={columns} label={item => item.name} canEdit={canEdit} storageKey="customers.columns.v1" refresh={resource.retry} onBusyChange={setBusy} scrollMode={compact ? "contained" : "page"}
        save={(item, field, value, key) => directoryClient.saveCustomer(item.id, { [field]: value, expected_updated_at: item.updatedAt }, key)} />
    </>}
  </section>;
}
