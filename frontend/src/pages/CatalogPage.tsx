import { useCallback, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { catalogClient, type CatalogCreate, type CatalogKind, type CatalogValue } from "../api/catalogClient";
import { ApiError } from "../api/errors";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { sessionGeneration } from "../auth/sessionTransport";
import { EditableResourceTable, type ResourceColumn, type ResourceValue } from "../components/EditableResourceTable";
import { ErrorState, LoadingState } from "../components/PageState";
import { useNavigationGuard } from "../navigationGuard";

type PendingCreate = { kind: CatalogKind; payload: CatalogCreate; generation: number };
function localDate(value: string | null) {
  if (!value) return null;
  const date = new Date(value);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
export function CatalogPage({ client }: { client: ApiClient }) {
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
  const [reason, setReason] = useState("Catalog table correction"), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false), [uncertain, setUncertain] = useState(false), [tableBusy, setTableBusy] = useState(false);
  const request = useRef<PendingCreate | null>(null), sending = useRef(false);
  useNavigationGuard(() => request.current !== null);
  const locked = pending || uncertain || tableBusy;
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
    { key: "value", label: "Name", value: item => item.value, render: item => <><strong>{item.value}</strong>{allowed && <button type="button" className="button" disabled={locked} aria-label={`Create replacement for ${item.value}`} onClick={() => { setReplacement(item.value); setValue(item.value); setBrand(item.brandId ?? ""); setCode(""); document.getElementById("catalog-new-value")?.focus(); }}>Create replacement</button>}</> },
    { key: "abbreviation", label: "Abbreviation", value: item => item.abbreviation, editable: true, bulk: true },
    { key: "type", label: "Type", value: () => kind === "online-categories" ? "Online category" : "Brand collection" },
    ...(kind === "collections" ? [{ key: "brand", label: "Brand", value: (item: CatalogValue) => brandName(item.brandId) }] : []),
    { key: "created", label: "Created", value: item => item.createdAt ? new Date(item.createdAt).toLocaleString() : null },
    { key: "is_active", label: "Active", type: "boolean", value: item => item.active, editable: true, bulk: true },
  ];
  const save = (row: CatalogValue, field: string, next: ResourceValue, key: string) => catalogClient.table(kind, row.id, {
    idempotency_key: key, expected_version: row.version, reason: reason.trim(),
    ...(field === "is_active" ? { is_active: next === true } : { abbreviation: next === null ? null : String(next).trim() || null }),
  });
  return <section className="catalog-content catalog-database"><div className="page-heading"><div><p className="eyebrow">Catalog</p><h1>Categories and collections</h1></div></div>
    <div role="tablist" aria-label="Catalog type" className="view-switch">
      <button role="tab" aria-selected={kind === "online-categories"} disabled={locked} onClick={() => { setKind("online-categories"); setReplacement(null); }}>Online categories</button>
      <button role="tab" aria-selected={kind === "collections"} disabled={locked} onClick={() => { setKind("collections"); setReplacement(null); }}>Brand collections</button>
    </div>
    <fieldset className="filters" disabled={locked}><legend>Filter catalog</legend>
      <label>Search catalog<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Name, abbreviation or brand" /></label>
      <label>Active filter<select value={activity} onChange={event => setActivity(event.target.value)}><option value="all">All</option><option value="active">Active</option><option value="inactive">Inactive</option></select></label>
      {kind === "collections" && <label>Brand filter<select value={brandFilter} onChange={event => setBrandFilter(event.target.value)}><option value="">All brands</option>{resource.data?.brands.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
      <label>Created from<input type="date" value={from} onChange={event => setFrom(event.target.value)} /></label><label>Created to<input type="date" value={to} onChange={event => setTo(event.target.value)} /></label>
      <label>Sort catalog<select value={sort} onChange={event => setSort(event.target.value)}><option value="name">Name</option><option value="abbreviation">Abbreviation</option><option value="created">Newest first</option></select></label>
      <button type="button" className="button" onClick={() => { setQuery(""); setActivity("all"); setBrandFilter(""); setFrom(""); setTo(""); }}>Clear filters</button>
    </fieldset>
    {error && <p role="alert" className="field-error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {uncertain && <button className="button" disabled={pending} onClick={() => void create()}>Retry same catalog request</button>}
    {resource.error ? <ErrorState message="Catalog values could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading catalog…" /> : <>
      <p>{filtered.length} of {items.length} values. Names and collection brands identify existing exports and remain stable. Use Create replacement for a different name; assignments remain with the original value.</p>
      {allowed && <div className="panel"><label>Reason for catalog change<input value={reason} disabled={locked} maxLength={2000} onChange={event => setReason(event.target.value)} /></label>
        <p>Changes to Active or Abbreviation reset checks and approvals for linked materials. Abbreviations use A–Z, 0–9, underscores or hyphens, up to 32 characters, and must be unique within this category list or collection brand. Applying the same abbreviation to several categories reports duplicate conflicts; an empty value clears their codes.</p>
      </div>}
      {!filtered.length && <p>No catalog values match these filters.</p>}
      <EditableResourceTable key={kind} rows={filtered} columns={columns} label={item => item.value} save={save} canEdit={allowed && Boolean(reason.trim()) && !pending && !uncertain} refresh={resource.retry} onBusyChange={setTableBusy} storageKey={`reawote-catalog-${kind}-columns-v1`} />
      {allowed && <article className="panel"><h2>{replacement ? `Create replacement for ${replacement}` : "Add catalog value"}</h2><form onSubmit={event => {
        event.preventDefault();
        if (value.includes(":")) { setError("Enter one value without a colon."); return; }
        void create({ kind, generation: sessionGeneration(), payload: { idempotency_key: crypto.randomUUID(), value: value.trim(), ...(code.trim() ? { abbreviation: code.trim() } : {}), ...(kind === "collections" ? { brand_id: brand } : {}) } });
      }}><fieldset disabled={locked}><legend>New value</legend>
        <label>Value type<select value={kind} onChange={event => { setKind(event.target.value as CatalogKind); setReplacement(null); }}><option value="online-categories">Online category</option><option value="collections">Brand collection</option></select></label>
        {kind === "collections" && <label>Collection brand<select required value={brand} onChange={event => setBrand(event.target.value)}><option value="">Choose a brand</option>{resource.data.brands.filter(item => item.isActive).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
        <label>Catalog value<input id="catalog-new-value" required maxLength={255} value={value} onChange={event => setValue(event.target.value)} /></label>
        <label>New abbreviation<input maxLength={32} pattern={"[A-Z0-9][A-Z0-9_\\-]*"} value={code} onChange={event => setCode(event.target.value)} /></label>
        <button className="button button--primary" disabled={!value.trim()}>Create catalog value</button>
      </fieldset></form></article>}
    </>}
  </section>;
}
