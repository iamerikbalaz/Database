import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { catalogClient, type CatalogCreate, type CatalogIdentityUpdate, type CatalogKind, type CatalogValue } from "../api/catalogClient";
import { ApiError } from "../api/errors";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { sessionGeneration } from "../auth/sessionTransport";
import { EditableResourceTable, type ResourceColumn, type ResourceValue } from "../components/EditableResourceTable";
import { Icon } from "../components/Icon";
import { ErrorState, LoadingState } from "../components/PageState";
import { useDatabaseWorkspace } from "../components/useDatabaseWorkspace";
import { ResponsiveFilters, type PriorityFilter } from "../components/ResponsiveFilters";
import { databaseSortOptions, sortDatabaseRecords, useDatabaseFilters } from "../components/useDatabaseFilters";
import "./CatalogPage.css";
import { useNavigationGuard } from "../navigationGuard";

type PendingCreate = { kind: CatalogKind; generation: number } & ({ action: "create"; payload: CatalogCreate } | { action: "edit"; itemId: string; payload: CatalogIdentityUpdate });
const filterDefaults = { kind: "online-categories", query: "", activity: "all", brandFilter: "", from: "", to: "", sort: "created-desc" };
function localDate(value: string | null) {
  if (!value) return null;
  const date = new Date(value);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
export function CatalogPage({ client }: { client: ApiClient }) {
  const compact = useDatabaseWorkspace();
  const role = useSession()?.session.user.role;
  const allowed = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const load = useCallback(async () => {
    const [categories, collections, brands] = await Promise.all([catalogClient.categories(), catalogClient.collections(), client.getBrands()]);
    return { categories, collections, brands };
  }, [client]);
  const resource = useResource(load);
  const { filters, setFilters, keepFilters, setKeepFilters } = useDatabaseFilters("catalog", filterDefaults);
  const { query, activity, brandFilter, from, to, sort } = filters;
  const kind: CatalogKind = filters.kind === "collections" ? "collections" : "online-categories";
  const setFilter = (field: keyof typeof filterDefaults, next: string) => setFilters(current => ({ ...current, [field]: next }));
  const [value, setValue] = useState(""), [brand, setBrand] = useState(""), [code, setCode] = useState("");
  const [editing, setEditing] = useState<CatalogValue | null>(null);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false), [uncertain, setUncertain] = useState(false), [tableBusy, setTableBusy] = useState(false);
  const request = useRef<PendingCreate | null>(null), sending = useRef(false);
  const createDialog = useRef<HTMLDialogElement>(null), helpDialog = useRef<HTMLDialogElement>(null);
  const createTrigger = useRef<HTMLButtonElement | null>(null), addTrigger = useRef<HTMLButtonElement>(null), valueInput = useRef<HTMLInputElement>(null);
  const restoreCreateFocus = useRef(false);
  useEffect(() => {
    if (!pending && resource.data && restoreCreateFocus.current) {
      restoreCreateFocus.current = false;
      (createTrigger.current?.isConnected ? createTrigger.current : addTrigger.current)?.focus();
    }
  }, [pending, resource.data]);
  useNavigationGuard(() => request.current !== null);
  const locked = pending || uncertain || tableBusy;
  const closeCreate = () => {
    if (sending.current || request.current) return;
    createDialog.current?.close(); createTrigger.current?.focus();
  };
  const openCreate = (trigger: HTMLButtonElement, item?: CatalogValue) => {
    if (locked) return;
    createTrigger.current = trigger;
    setEditing(item ?? null); setValue(item?.value ?? ""); setBrand(item?.brandId ?? ""); setCode(item?.abbreviation ?? ""); setError("");
    createDialog.current?.showModal(); valueInput.current?.focus();
  };
  const create = async (change?: PendingCreate) => {
    if (sending.current) return;
    request.current ??= change ?? null;
    if (!request.current || request.current.generation !== sessionGeneration()) return;
    sending.current = true; setPending(true); setError(""); setNotice("");
    const wasUncertain = uncertain;
    try {
      const current = request.current;
      if (current.action === "edit") await catalogClient.identity(current.kind, current.itemId, current.payload);
      else await catalogClient.create(current.kind, current.payload);
      request.current = null; setUncertain(false); setValue(""); setCode("");
      setNotice(current.action === "edit" ? "Catalog change saved. Existing material folders and file names were not changed." : "Catalog change saved.");
      createDialog.current?.close(); restoreCreateFocus.current = true;
      setEditing(null); resource.retry();
    } catch (cause) {
      if (!wasUncertain && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        request.current = null; setUncertain(false);
        setError("The catalog value was rejected. Check for duplicate names or abbreviations, inactive brands and the abbreviation format.");
      } else {
        setUncertain(true); setError("The outcome is unknown. Retry the same catalog request before creating another value.");
      }
    } finally { sending.current = false; setPending(false); }
  };
  const brandName = (id: string | null) => resource.data?.brands.find(item => item.id === id)?.name ?? "Brand unavailable";
  const items = resource.data ? kind === "online-categories" ? resource.data.categories : resource.data.collections : [];
  const search = query.trim().toLocaleLowerCase();
  const filtered = sortDatabaseRecords(items.filter(item => (!search || [item.value, item.abbreviation ?? "", item.brandId ? brandName(item.brandId) : ""].some(text => text.toLocaleLowerCase().includes(search))) &&
    (activity === "all" || item.active === (activity === "active")) && (!brandFilter || kind === "online-categories" || item.brandId === brandFilter) &&
    (!from || Boolean(item.createdAt && localDate(item.createdAt)! >= from)) && (!to || Boolean(item.createdAt && localDate(item.createdAt)! <= to))), sort, item => item.value, item => item.createdAt);
  const columns: ResourceColumn<CatalogValue>[] = [
    { key: "value", label: "Name", value: item => item.value, render: item => <div className="catalog-name-cell"><strong>{item.value}</strong>{allowed && <button type="button" className="button" disabled={locked} aria-label={`Edit ${kind === "online-categories" ? "category" : "collection"} ${item.value}`} onClick={event => openCreate(event.currentTarget, item)}>Edit {kind === "online-categories" ? "category" : "collection"}</button>}</div> },
    { key: "abbreviation", label: "Abbreviation", value: item => item.abbreviation },
    { key: "type", label: "Type", value: () => kind === "online-categories" ? "Online category" : "Brand collection" },
    ...(kind === "collections" ? [{ key: "brand", label: "Customer", value: (item: CatalogValue) => brandName(item.brandId) }] : []),
    { key: "created", label: "Created", value: item => item.createdAt ? new Date(item.createdAt).toLocaleString() : null },
    { key: "is_active", label: "Active", type: "boolean", value: item => item.active, editable: true },
  ];
  const save = (row: CatalogValue, field: string, next: ResourceValue, key: string) => catalogClient.table(kind, row.id, {
    idempotency_key: key, expected_version: row.version,
    ...(field === "is_active" ? { is_active: next === true } : { abbreviation: next === null ? null : String(next).trim() || null }),
  });
  return <section className={`database-page catalog-content catalog-database${compact ? " database-page--workspace" : ""}`}><div className="page-heading"><div><p className="eyebrow">Catalog</p><h1>Categories and collections</h1></div>
      <div className="database-heading-actions"><button className="button" onClick={() => helpDialog.current?.showModal()}>Catalog help</button>{allowed && <button ref={addTrigger} className="button button--primary" disabled={locked || !resource.data} onClick={event => openCreate(event.currentTarget)}><Icon name="plus" size={16} />Add catalog value</button>}</div>
    </div>
    <div role="tablist" aria-label="Catalog type" className="view-switch">
      <button role="tab" aria-selected={kind === "online-categories"} disabled={locked} onClick={() => setFilter("kind", "online-categories")}>Online categories</button>
      <button role="tab" aria-selected={kind === "collections"} disabled={locked} onClick={() => setFilter("kind", "collections")}>Brand collections</button>
    </div>
    <ResponsiveFilters compact={compact} disabled={locked} label="Filter catalog" keepFilters={keepFilters} onKeepFiltersChange={setKeepFilters} onClear={() => setFilters(current => ({ ...filterDefaults, kind: current.kind, sort: current.sort }))} filters={[
      { key: "search", width: 190, active: Boolean(query), content: <label className="database-search">Search catalog<span className="database-search-input"><Icon name="search" size={18} /><input type="search" value={query} onChange={event => setFilter("query", event.target.value)} placeholder="Name, abbreviation or customer" /></span></label> },
      { key: "active", width: 110, active: activity !== "all", content: <label>Active filter<select value={activity} onChange={event => setFilter("activity", event.target.value)}><option value="all">All</option><option value="active">Active</option><option value="inactive">Inactive</option></select></label> },
      ...(kind === "collections" ? [{ key: "customer", width: 140, active: Boolean(brandFilter), content: <label>Customer filter<select value={brandFilter} onChange={event => setFilter("brandFilter", event.target.value)}><option value="">All customers</option>{resource.data?.brands.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> }] satisfies PriorityFilter[] : []),
      { key: "sort", width: 165, active: sort !== "created-desc", content: <label>Sort catalog<select value={sort} onChange={event => setFilter("sort", event.target.value)}>{databaseSortOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label> },
      { key: "from", width: 142, active: Boolean(from), content: <label>Created from<input type="date" value={from} onChange={event => setFilter("from", event.target.value)} /></label> },
      { key: "to", width: 142, active: Boolean(to), content: <label>Created to<input type="date" value={to} onChange={event => setFilter("to", event.target.value)} /></label> },
    ]} />
    {notice && <p role="status">{notice}</p>}
    {resource.error ? <ErrorState message="Catalog values could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading catalog…" /> : <>
      <p className="result-count">{filtered.length} of {items.length} values</p>
      {!filtered.length && <p>No catalog values match these filters.</p>}
      <EditableResourceTable key={kind} rows={filtered} columns={columns} label={item => item.value} save={save} canEdit={allowed && !pending && !uncertain} refresh={resource.retry} onBusyChange={setTableBusy} storageKey={`reawote-catalog-${kind}-columns-v1`} scrollMode={compact ? "contained" : "page"} />
      {allowed && <dialog ref={createDialog} className="resource-bulk-dialog catalog-create-dialog" aria-labelledby="catalog-create-title" onCancel={event => { event.preventDefault(); closeCreate(); }}><h2 id="catalog-create-title">{editing ? `Edit ${kind === "online-categories" ? "category" : "collection"}` : "Add catalog value"}</h2>
      {editing && <p className="catalog-edit-warning">Editing existing categories or collections is not recommended. Deactivate the old value and add a new one when possible. This change will not rename existing material folders or files. New names and abbreviations will be available for future materials.</p>}
      <form className="record-form" onSubmit={event => {
        event.preventDefault();
        if (locked || request.current) return;
        if (value.includes(":")) { setError("Enter one value without a colon."); return; }
        void create(editing ? { action: "edit", kind, itemId: editing.id, generation: sessionGeneration(), payload: { idempotency_key: crypto.randomUUID(), expected_version: editing.version, value: value.trim(), abbreviation: code.trim() || null } } : { action: "create", kind, generation: sessionGeneration(), payload: { idempotency_key: crypto.randomUUID(), value: value.trim(), ...(code.trim() ? { abbreviation: code.trim() } : {}), ...(kind === "collections" ? { brand_id: brand } : {}) } });
      }}><fieldset disabled={locked}><legend>New value</legend>
        <label>Value type<select value={kind} disabled={Boolean(editing)} onChange={event => setFilter("kind", event.target.value)}><option value="online-categories">Online category</option><option value="collections">Brand collection</option></select></label>
        {kind === "collections" && !editing && <label>Collection customer<select required value={brand} onChange={event => setBrand(event.target.value)}><option value="">Choose a customer</option>{resource.data.brands.filter(item => item.isActive).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
        <label>Catalog value<input ref={valueInput} id="catalog-new-value" required maxLength={255} value={value} onChange={event => setValue(event.target.value)} /></label>
        <label>New abbreviation<input maxLength={32} pattern={"[A-Z0-9][A-Z0-9_\\-]*"} value={code} onChange={event => setCode(event.target.value)} /></label>
        <button className="button button--primary" disabled={!value.trim()}>{editing ? "Save catalog changes" : "Create catalog value"}</button>
      </fieldset></form>
        {error && <p role="alert" className="field-error">{error}</p>}
        {pending && <p role="status">Saving catalog value…</p>}
        {uncertain && <button className="button" disabled={pending} onClick={() => void create()}>Retry same catalog request</button>}
        <button type="button" className="button" disabled={pending || uncertain} onClick={closeCreate}>Close</button>
      </dialog>}
    </>}
    <dialog ref={helpDialog} className="resource-bulk-dialog" aria-labelledby="catalog-help-title"><h2 id="catalog-help-title">Catalog help</h2>
      <p>Prefer deactivating an old category or collection and adding a new value. Name and abbreviation corrections are available in the Edit dialog and never rename existing material folders or files.</p>
      {allowed && <p>Catalog changes reset checks and approvals for linked materials. Abbreviations use A–Z, 0–9, underscores or hyphens, up to 32 characters, and must be unique within this category list or collection brand. Main category abbreviations used in material folder names support letters, digits and hyphens. Catalog values cannot be changed in bulk.</p>}
      <button type="button" className="button" onClick={() => helpDialog.current?.close()}>Close</button>
    </dialog>
  </section>;
}
