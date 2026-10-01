import { useCallback, useState } from "react";

import type { ApiClient } from "../api/client";

import { directoryClient, orderStatuses, priorities, type Order } from "../api/directoryClient";

import { useResource } from "../api/useResource";

import { useSession } from "../auth/context";

import { EditableResourceTable, type ResourceColumn } from "../components/EditableResourceTable";

import { DirectorySync } from "../components/DirectorySync";

import { NavigationLink } from "../components/NavigationLink";

import { NasFolderReference } from "../components/NasFolderReference";

import { ErrorState, LoadingState } from "../components/PageState";

import { Icon } from "../components/Icon";
import { useDatabaseWorkspace } from "../components/useDatabaseWorkspace";
import { KeepFiltersControl, ResponsiveFilters } from "../components/ResponsiveFilters";
import { numberedDatabaseSortOptions, sortDatabaseRecords, useDatabaseFilters } from "../components/useDatabaseFilters";
import { DatabaseResultsToolbar } from "../components/DatabaseResultsToolbar";



export function OrdersPage({ client, navigate }: { client: ApiClient; navigate: (path: string) => void }) {
  const compact = useDatabaseWorkspace();

  const resource = useResource(useCallback(() => Promise.all([directoryClient.orders(), directoryClient.customers(), client.getInternalUsers()]), [client]));

  const { filters, setFilters, keepFilters, setKeepFilters, resetFilters } = useDatabaseFilters("orders", { search: "", status: "", customer: "", priority: "", responsible: "", from: "", to: "", sort: "number-desc" });
  const { search, status, customer, priority, responsible, from, to, sort } = filters;
  const [busy, setBusy] = useState(false);
  const changeFilter = (key: keyof typeof filters, value: string) => setFilters(current => ({ ...current, [key]: value }));
  const setSearch = (value: string) => changeFilter("search", value), setStatus = (value: string) => changeFilter("status", value), setCustomer = (value: string) => changeFilter("customer", value), setPriority = (value: string) => changeFilter("priority", value), setResponsible = (value: string) => changeFilter("responsible", value), setFrom = (value: string) => changeFilter("from", value), setTo = (value: string) => changeFilter("to", value);

  const role = useSession()?.session.user.role, canEdit = role === "ADMIN" || role === "PRODUCTION_LEAD";

  const [all = [], customers = [], users = []] = resource.data ?? [];

  const names = new Map(customers.map(item => [item.id, item.name]));

  const matching = all.filter(item => (!status || item.status === status) && (!customer || item.customerId === customer) && (!priority || item.priority === priority) && (!responsible || item.responsibleId === responsible) &&

    (!from || Boolean(item.startingDate && item.startingDate >= from)) && (!to || Boolean(item.dueDate && item.dueDate <= to)) &&

    [item.number, item.generatedName, item.projectType, item.notes, names.get(item.customerId ?? "")].some(value => value?.toLowerCase().includes(search.toLowerCase())));
  const filtered = sortDatabaseRecords(matching, sort, item => item.generatedName, item => item.createdAt, item => item.number);

  const columns: ResourceColumn<Order>[] = [

    { key: "number", label: "NUMBER", value: item => item.number, render: item => <NavigationLink className="table-link" href={`/orders/${item.id}`} navigate={navigate}>{item.number}</NavigationLink> },

    { key: "customer_id", label: "Customer", value: item => item.customerId, editable: true, bulk: true, options: [{ value: "", label: "Not assigned", disabled: true }, ...customers.map(item => ({ value: item.id, label: item.name }))] },

    { key: "project_type", label: "Project type", value: item => item.projectType, editable: true, bulk: true },

    { key: "starting_date", label: "Starting date", value: item => item.startingDate, type: "date", editable: true, bulk: true },

    { key: "due_date", label: "Due date", value: item => item.dueDate, type: "date", editable: true, bulk: true },

    { key: "status", label: "Status", value: item => item.status, editable: true, bulk: true, options: orderStatuses.map(value => ({ value, label: value })) },

    { key: "priority", label: "Priority", value: item => item.priority, editable: true, bulk: true, options: [{ value: "", label: "Not set" }, ...priorities.map(value => ({ value, label: value }))] },

    { key: "responsible_id", label: "Responsible", value: item => item.responsibleId, editable: true, bulk: true, options: [{ value: "", label: "Not assigned" }, ...users.filter(item => item.role === "PROCESSOR" && item.isActive || all.some(order => order.responsibleId === item.id)).map(item => ({ value: item.id, label: item.displayName + (item.isActive ? "" : " (inactive)"), disabled: !item.isActive || item.role !== "PROCESSOR", currentOnly: !item.isActive || item.role !== "PROCESSOR" }))] },

    { key: "notes", label: "Note", value: item => item.notes, type: "textarea", editable: true, bulk: true },

    { key: "generated_name", label: "Generated name", value: item => item.generatedName },

    { key: "folder_path", label: "NAS folder", value: item => item.folderPath, render: item => <><NasFolderReference path={item.folderPath} />{item.folderPath && !item.folderNameMatches && <NavigationLink className="folder-rename-notice" href={`/orders/${item.id}`} navigate={navigate}>Folder rename needs confirmation</NavigationLink>}</> },

    { key: "sync", label: "Notion", value: item => item.sync?.state ?? null, render: item => <DirectorySync sync={item.sync} notionPageId={item.notionPageId} /> },

    { key: "created", label: "Created", value: item => item.createdAt }, { key: "updated", label: "Updated", value: item => item.updatedAt },

  ];

  return <section className={`database-page orders-page${compact ? " database-page--workspace" : ""}`}><div className="page-heading"><div><p className="eyebrow">Production</p><h1>Orders</h1><p className="database-description">Customer work, delivery dates and project folders.</p></div><div className="database-heading-actions">{canEdit && <NavigationLink className="button button--primary" href="/orders/new" navigate={navigate}><Icon name="plus" size={18} />Add order</NavigationLink>}<KeepFiltersControl checked={keepFilters} onChange={setKeepFilters} disabled={busy} /></div></div>

    <ResponsiveFilters compact={compact} disabled={busy} label="Filter orders" onClear={resetFilters} filters={[
      { key: "search", width: 190, active: Boolean(search), content: <label className="database-search">Search orders<span className="database-search-input"><Icon name="search" size={18} /><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Number, name or note" /></span></label> },
      { key: "customer", width: 135, active: Boolean(customer), content: <label>Customer<select value={customer} onChange={event => setCustomer(event.target.value)}><option value="">All</option>{customers.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> },
      { key: "status", width: 115, active: Boolean(status), content: <label>Status<select value={status} onChange={event => setStatus(event.target.value)}><option value="">All</option>{orderStatuses.map(value => <option key={value}>{value}</option>)}</select></label> },
      { key: "responsible", width: 145, active: Boolean(responsible), content: <label>Responsible<select value={responsible} onChange={event => setResponsible(event.target.value)}><option value="">All</option>{users.map(item => <option key={item.id} value={item.id}>{item.displayName}</option>)}</select></label> },
      { key: "from", width: 142, active: Boolean(from), content: <label>Starting from<input type="date" value={from} onChange={event => setFrom(event.target.value)} /></label> },
      { key: "due", width: 142, active: Boolean(to), content: <label>Due date<input type="date" value={to} onChange={event => setTo(event.target.value)} /></label> },
      { key: "priority", width: 115, active: Boolean(priority), content: <label>Priority<select value={priority} onChange={event => setPriority(event.target.value)}><option value="">All</option>{priorities.map(value => <option key={value}>{value}</option>)}</select></label> },
    ]} />
    <DatabaseResultsToolbar count={`${filtered.length} orders`} sort={sort} sortLabel="Sort orders" options={numberedDatabaseSortOptions}
      disabled={busy} onSortChange={value => changeFilter("sort", value)} />

    {resource.error ? <ErrorState message="Orders could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading orders…" /> : <>

      {!filtered.length && <p>No orders match these filters.</p>}

      <EditableResourceTable rows={filtered} columns={columns} label={item => `${item.number} · ${item.generatedName}`} canEdit={canEdit} storageKey="orders.columns.v1" refresh={resource.retry} onBusyChange={setBusy} scrollMode={compact ? "contained" : "page"}

        save={(item, field, value, key) => directoryClient.saveOrder(item.id, { [field]: value, expected_updated_at: item.updatedAt }, key)} />

    </>}

  </section>;

}
