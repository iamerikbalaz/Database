import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { catalogClient, type CatalogCreate, type CatalogKind, type CatalogValue } from "../api/catalogClient";
import { ApiError } from "../api/errors";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { sessionGeneration } from "../auth/sessionTransport";
import { EditableResourceTable, type ResourceColumn, type ResourceValue } from "../components/EditableResourceTable";
import { Icon } from "../components/Icon";
import { ErrorState, LoadingState } from "../components/PageState";
import { useDatabaseWorkspace } from "../components/useDatabaseWorkspace";
import { useNavigationGuard } from "../navigationGuard";

type PendingCreate = { kind: CatalogKind; payload: CatalogCreate; generation: number };
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
  const [kind, setKind] = useState<CatalogKind>("online-categories");
  const [query, setQuery] = useState(""), [activity, setActivity] = useState("all"), [brandFilter, setBrandFilter] = useState("");
  const [from, setFrom] = useState(""), [to, setTo] = useState(""), [sort, setSort] = useState("name");
  const [value, setValue] = useState(""), [brand, setBrand] = useState(""), [code, setCode] = useState("");
  const [replacement, setReplacement] = useState<string | null>(null);
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
    setReplacement(item?.value ?? null); setValue(item?.value ?? ""); setBrand(item?.brandId ?? ""); setCode(""); setError("");
    createDialog.current?.showModal(); valueInput.current?.focus();
  };
  const create = async (change?: PendingCreate) => {
    if (sending.current) return;
    request.current ??= change ?? null;
    if (!request.current || request.current.generation !== sessionGeneration()) return;
    sending.current = true; setPending(true); setError(""); setNotice("");
    const wasUncertain = uncertain;
    try {
      await catalogClient.create(request.current.kind, request.current.payload);
      request.current = null; setUncertain(false); setValue(""); setCode("");
      setNotice(replacement ? `Replacement created. Existing materials still use ${replacement}; deactivate that value when appropriate.` : "Catalog change saved.");
      createDialog.current?.close(); restoreCreateFocus.current = true;
      setReplacement(null); resource.retry();
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
  const filtered = items.filter(item => (!search || [item.value, item.abbreviation ?? "", item.brandId ? brandName(item.brandId) : ""].some(text => text.toLocaleLowerCase().includes(search))) &&
    (activity === "all" || item.active === (activity === "active")) && (!brandFilter || kind === "online-categories" || item.brandId === brandFilter) &&
    (!from || Boolean(item.createdAt && localDate(item.createdAt)! >= from)) && (!to || Boolean(item.createdAt && localDate(item.createdAt)! <= to)))
    .sort((a, b) => (sort === "created" ? (b.createdAt ?? "").localeCompare(a.createdAt ?? "") : sort === "abbreviation" ? (a.abbreviation ?? "").localeCompare(b.abbreviation ?? "", undefined, { numeric: true }) : a.value.localeCompare(b.value)) || a.id.localeCompare(b.id));
  const columns: ResourceColumn<CatalogValue>[] = [
    { key: "value", label: "Name", value: item => item.value, render: item => <><strong>{item.value}</strong>{allowed && <button type="button" className="button" disabled={locked} aria-label={`Create replacement for ${item.value}`} onClick={event => openCreate(event.currentTarget, item)}>Create replacement</button>}</> },
    { key: "abbreviation", label: "Abbreviation", value: item => item.abbreviation, editable: true, bulk: true },
    { key: "type", label: "Type", value: () => kind === "online-categories" ? "Online category" : "Brand collection" },
    ...(kind === "collections" ? [{ key: "brand", label: "Customer", value: (item: CatalogValue) => brandName(item.brandId) }] : []),
    { key: "created", label: "Created", value: item => item.createdAt ? new Date(item.createdAt).toLocaleString() : null },
    { key: "is_active", label: "Active", type: "boolean", value: item => item.active, editable: true, bulk: true },
  ];
  const save = (row: CatalogValue, field: string, next: ResourceValue, key: string) => catalogClient.table(kind, row.id, {
    idempotency_key: key, expected_version: row.version,
    ...(field === "is_active" ? { is_active: next === true } : { abbreviation: next === null ? null : String(next).trim() || null }),
  });
  const dateCount = [from, to].filter(Boolean).length;
  return <section className={`database-page catalog-content catalog-database${compact ? " database-page--workspace" : ""}`}><div className="page-heading"><div><p className="eyebrow">Catalog</p><h1>Categories and collections</h1></div>
      <div className="database-heading-actions"><button className="button" onClick={() => helpDialog.current?.showModal()}>Catalog help</button>{allowed && <button ref={addTrigger} className="button button--primary" disabled={locked || !resource.data} onClick={event => openCreate(event.currentTarget)}><Icon name="plus" size={16} />Add catalog value</button>}</div>
    </div>
    <div role="tablist" aria-label="Catalog type" className="view-switch">
      <button role="tab" aria-selected={kind === "online-categories"} disabled={locked} onClick={() => { setKind("online-categories"); setReplacement(null); }}>Online categories</button>
      <button role="tab" aria-selected={kind === "collections"} disabled={locked} onClick={() => { setKind("collections"); setReplacement(null); }}>Brand collections</button>
    </div>
    <fieldset className="material-filters database-filters" disabled={locked}><legend className="sr-only">Filter catalog</legend>
      <label className="database-search">Search catalog<span className="database-search-input"><Icon name="search" size={18} /><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Name, abbreviation or customer" /></span></label>
      <label>Active filter<select value={activity} onChange={event => setActivity(event.target.value)}><option value="all">All</option><option value="active">Active</option><option value="inactive">Inactive</option></select></label>
      {kind === "collections" && <label>Customer filter<select value={brandFilter} onChange={event => setBrandFilter(event.target.value)}><option value="">All customers</option>{resource.data?.brands.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
      <label>Sort catalog<select value={sort} onChange={event => setSort(event.target.value)}><option value="name">Name</option><option value="abbreviation">Abbreviation</option><option value="created">Newest first</option></select></label>
      <details className="database-date-filters" open={compact ? undefined : true} onKeyDown={event => { if (event.key === "Escape" && compact) { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); } }}><summary>Dates{dateCount > 0 ? ` (${dateCount})` : ""}</summary><div className="database-date-options">
        <label>Created from<input type="date" value={from} onChange={event => setFrom(event.target.value)} /></label><label>Created to<input type="date" value={to} onChange={event => setTo(event.target.value)} /></label>
      </div></details>
      <button type="button" className="button" onClick={() => { setQuery(""); setActivity("all"); setBrandFilter(""); setFrom(""); setTo(""); }}>Clear filters</button>
    </fieldset>
    {notice && <p role="status">{notice}</p>}
    {resource.error ? <ErrorState message="Catalog values could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading catalog…" /> : <>
      <p className="result-count">{filtered.length} of {items.length} values</p>
      {!filtered.length && <p>No catalog values match these filters.</p>}
      <EditableResourceTable key={kind} rows={filtered} columns={columns} label={item => item.value} save={save} canEdit={allowed && !pending && !uncertain} refresh={resource.retry} onBusyChange={setTableBusy} storageKey={`reawote-catalog-${kind}-columns-v1`} scrollMode={compact ? "contained" : "page"} />
      {allowed && <dialog ref={createDialog} className="resource-bulk-dialog catalog-create-dialog" aria-labelledby="catalog-create-title" onCancel={event => { event.preventDefault(); closeCreate(); }}><h2 id="catalog-create-title">{replacement ? `Create replacement for ${replacement}` : "Add catalog value"}</h2><form className="record-form" onSubmit={event => {
        event.preventDefault();
        if (locked || request.current) return;
        if (value.includes(":")) { setError("Enter one value without a colon."); return; }
        void create({ kind, generation: sessionGeneration(), payload: { idempotency_key: crypto.randomUUID(), value: value.trim(), ...(code.trim() ? { abbreviation: code.trim() } : {}), ...(kind === "collections" ? { brand_id: brand } : {}) } });
      }}><fieldset disabled={locked}><legend>New value</legend>
        <label>Value type<select value={kind} onChange={event => { setKind(event.target.value as CatalogKind); setReplacement(null); }}><option value="online-categories">Online category</option><option value="collections">Brand collection</option></select></label>
        {kind === "collections" && <label>Collection customer<select required value={brand} onChange={event => setBrand(event.target.value)}><option value="">Choose a customer</option>{resource.data.brands.filter(item => item.isActive).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
        <label>Catalog value<input ref={valueInput} id="catalog-new-value" required maxLength={255} value={value} onChange={event => setValue(event.target.value)} /></label>
        <label>New abbreviation<input maxLength={32} pattern={"[A-Z0-9][A-Z0-9_\\-]*"} value={code} onChange={event => setCode(event.target.value)} /></label>
        <button className="button button--primary" disabled={!value.trim()}>Create catalog value</button>
      </fieldset></form>
        {error && <p role="alert" className="field-error">{error}</p>}
        {pending && <p role="status">Saving catalog value…</p>}
        {uncertain && <button className="button" disabled={pending} onClick={() => void create()}>Retry same catalog request</button>}
        <button type="button" className="button" disabled={pending || uncertain} onClick={closeCreate}>Close</button>
      </dialog>}
    </>}
    <dialog ref={helpDialog} className="resource-bulk-dialog" aria-labelledby="catalog-help-title"><h2 id="catalog-help-title">Catalog help</h2>
      <p>Names and collection brands identify existing exports and remain stable. Use Create replacement for a different name; assignments remain with the original value.</p>
      {allowed && <p>Changes to Active or Abbreviation reset checks and approvals for linked materials. Abbreviations use A–Z, 0–9, underscores or hyphens, up to 32 characters, and must be unique within this category list or collection brand. Applying the same abbreviation to several categories reports duplicate conflicts; an empty value clears their codes.</p>}
      <button type="button" className="button" onClick={() => helpDialog.current?.close()}>Close</button>
    </dialog>
  </section>;
}
