import { useCallback, useState } from "react";
import { directoryClient, customerStatuses, type Customer } from "../api/directoryClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { EditableResourceTable, type ResourceColumn } from "../components/EditableResourceTable";
import { DirectorySync } from "../components/DirectorySync";
import { NavigationLink } from "../components/NavigationLink";
import { ErrorState, LoadingState } from "../components/PageState";
import { Icon } from "../components/Icon";
import { categoryLabel, catalogMaterialCategoryLabels } from "../data/materialCategories";
import { catalogClient } from "../api/catalogClient";
import { useDatabaseWorkspace } from "../components/useDatabaseWorkspace";
import { useDatabaseFilters, sortDatabaseRecords } from "../components/useDatabaseFilters";
import { DatabaseResultsToolbar } from "../components/DatabaseResultsToolbar";
import { KeepFiltersControl, ResponsiveFilters, type PriorityFilter } from "../components/ResponsiveFilters";
import { CustomerCsvExport } from "../components/CustomerCsvExport";

const defaults = { search: "", status: "", category: "", published: "", createdFrom: "", createdTo: "", updatedFrom: "", updatedTo: "", sort: "name-asc" };

export function CustomersPage({ navigate }: { navigate: (path: string) => void }) {
  const resource = useResource(useCallback(() => directoryClient.customers(), []));
  const catalog = useResource(catalogClient.categories);
  const categoryLabels = catalogMaterialCategoryLabels(catalog.data ?? []);
  const categoryKey = (code: string) => categoryLabels.find(item => item.code === code || item.aliases?.includes(code))?.code || code;
  const [busy, setBusy] = useState(false);
  const { filters, setFilters, keepFilters, setKeepFilters, resetFilters } = useDatabaseFilters("customers", defaults);
  const { search, status, category, published, sort } = filters, dates = filters;
  const set = (key: keyof typeof defaults, value: string) => setFilters(previous => ({ ...previous, [key]: value }));
  const compact = useDatabaseWorkspace();
  const role = useSession()?.session.user.role, canEdit = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const all = resource.data ?? [], categories = [...new Set([...all.flatMap(item => item.mainCategoryCodes.map(categoryKey)), ...(category ? [category] : [])])].sort();
  const filtered = sortDatabaseRecords(all.filter(item => (!status || item.status === status) && (!category || item.mainCategoryCodes.some(code => categoryKey(code) === categoryKey(category))) && (!published || item.isPublished === (published === "true")) &&
    (!dates.createdFrom || item.createdAt.slice(0, 10) >= dates.createdFrom) && (!dates.createdTo || item.createdAt.slice(0, 10) <= dates.createdTo) &&
    (!dates.updatedFrom || item.updatedAt.slice(0, 10) >= dates.updatedFrom) && (!dates.updatedTo || item.updatedAt.slice(0, 10) <= dates.updatedTo) &&
    [item.name, item.legalName, item.brandIdentifier, item.address, item.shippingAddress, item.vatId, item.website, item.description, item.notes].some(value => value?.toLowerCase().includes(search.toLowerCase()))), sort, item => item.name, item => item.createdAt);
  const columns: ResourceColumn<Customer>[] = [
    { key: "logo", label: "Logo", required: true, value: () => null, render: item => item.hasLogo ? <img className="customer-logo customer-logo--thumbnail" src={directoryClient.logoUrl(item)} alt={`${item.name} logo`} loading="lazy" /> : <span className="company-monogram">{item.name.slice(0, 2).toUpperCase()}</span> },
    { key: "name", label: "Customer", value: item => item.name, render: item => <NavigationLink className="table-link" href={`/customers/${item.id}`} navigate={navigate}>{item.name}</NavigationLink> },
    { key: "brand_identifier", label: "Brand identifier", value: item => item.brandIdentifier, editable: true },
    { key: "status", label: "Status", value: item => item.status, editable: true, bulk: true, options: customerStatuses.map(value => ({ value, label: value })) },
    { key: "categories", label: "Main categories", value: item => item.mainCategoryCodes.map(code => categoryLabel(code, categoryLabels)).join(", ") },
    { key: "website", label: "Website", value: item => item.website, editable: true },
    { key: "country", label: "Country", value: item => item.country, editable: true },
    { key: "is_published", label: "Published", value: item => item.isPublished, type: "boolean", editable: true, bulk: true },
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
  const filterFields: PriorityFilter[] = [
    { key: "search", width: 205, active: Boolean(search), content: <label className="database-search">Search customers<span className="database-search-input"><Icon name="search" size={18} /><input type="search" value={search} onChange={event => set("search", event.target.value)} placeholder="Name, identifier or notes" /></span></label> },
    { key: "status", width: 180, active: Boolean(status), content: <label>Status<select value={status} onChange={event => set("status", event.target.value)}><option value="">All</option>{customerStatuses.map(value => <option key={value}>{value}</option>)}</select></label> },
    { key: "category", width: 175, active: Boolean(category), content: <label>Main category<select value={category} onChange={event => set("category", event.target.value)}><option value="">All categories</option>{categories.map(value => <option key={value} value={value}>{categoryLabel(value, categoryLabels)}</option>)}</select></label> },
    { key: "published", width: 115, active: Boolean(published), content: <label>Published<select value={published} onChange={event => set("published", event.target.value)}><option value="">All</option><option value="true">Yes</option><option value="false">No</option></select></label> },
    ...([["createdFrom", "Created from"], ["createdTo", "Created to"], ["updatedFrom", "Updated from"], ["updatedTo", "Updated to"]] as const).map(([key, label]) => ({ key, width: 150, active: Boolean(dates[key]), content: <label>{label}<input type="date" value={dates[key]} onChange={event => set(key, event.target.value)} /></label> })),
  ];
  return <section className={`database-page customers-page${compact ? " database-page--workspace" : ""}`}><div className="page-heading"><div><p className="eyebrow">Directory</p><h1>Customers</h1><p className="database-description">Customer profiles, materials and orders.</p></div><div className="database-heading-actions">{canEdit && <NavigationLink className="button button--primary" href="/customers/new" navigate={navigate}><Icon name="plus" size={18} />Add customer</NavigationLink>}<KeepFiltersControl checked={keepFilters} onChange={setKeepFilters} disabled={busy} /></div></div>
    <ResponsiveFilters compact={compact} filters={filterFields} disabled={busy} label="Filter customers" onClear={resetFilters} />
    <DatabaseResultsToolbar count={`${filtered.length} customers`} sort={sort} sortLabel="Sort by" disabled={busy} onSortChange={value => set("sort", value)} />
    {resource.error ? <ErrorState message="Customers could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading customers…" /> : <>
      {!filtered.length && <p>No customers match these filters.</p>}
      <EditableResourceTable rows={filtered} columns={columns} label={item => item.name} canEdit={canEdit} storageKey="customers.columns.v1" refresh={resource.retry} onBusyChange={setBusy} scrollMode={compact ? "contained" : "page"}
        selectionActions={canEdit ? (rows, active) => <CustomerCsvExport rows={rows} disabled={active} /> : undefined}
        save={(item, field, value, key) => directoryClient.saveCustomer(item.id, { [field]: value, expected_updated_at: item.updatedAt }, key)} />
    </>}
  </section>;
}
