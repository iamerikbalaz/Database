import { useCallback, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { catalogClient, type CatalogActivity, type CatalogCreate, type CatalogKind, type CatalogValue } from "../api/catalogClient";
import { ApiError } from "../api/errors";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ErrorState, LoadingState } from "../components/PageState";

type PendingChange = { kind: CatalogKind; create: CatalogCreate } | { kind: CatalogKind; id: string; activity: CatalogActivity };
export function CatalogPage({ client }: { client: ApiClient }) {
  const role = useSession()?.session.user.role;
  const allowed = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const load = useCallback(async () => {
    const [categories, collections, brands] = await Promise.all([catalogClient.categories(), catalogClient.collections(), client.getBrands()]);
    return { categories, collections, brands };
  }, [client]);
  const resource = useResource(load);
  const [kind, setKind] = useState<CatalogKind>("online-categories");
  const [value, setValue] = useState(""); const [brand, setBrand] = useState("");
  const [selected, setSelected] = useState<{ kind: CatalogKind; item: CatalogValue } | null>(null);
  const [reason, setReason] = useState(""); const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false); const [uncertain, setUncertain] = useState(false);
  const request = useRef<PendingChange | null>(null); const sending = useRef(false);
  const run = async (change?: PendingChange) => {
    if (sending.current) return;
    request.current ??= change ?? null;
    if (!request.current) return;
    sending.current = true; setPending(true); setError(""); setNotice("");
    try {
      const payload = request.current;
      if ("create" in payload) await catalogClient.create(payload.kind, payload.create);
      else await catalogClient.activity(payload.kind, payload.id, payload.activity);
      request.current = null; setUncertain(false); setSelected(null); setReason(""); setValue("");
      setNotice("Catalog change saved."); resource.retry();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        request.current = null; setUncertain(false);
        setError("The catalog change was rejected. Check for duplicate values, inactive brands or a newer version. Materials with active source operations must be reconciled first.");
      } else {
        setUncertain(true); setError("The outcome is unknown. Retry the same catalog request before making another change.");
      }
    } finally { sending.current = false; setPending(false); }
  };
  const renderList = (title: string, listKind: CatalogKind, items: CatalogValue[]) => <article className="panel" aria-label={title}><h2>{title}</h2>
    {items.length === 0 ? <p>No values yet.</p> : <ul>{items.map((item) => <li key={item.id}>
      <strong>{item.value}</strong> · {item.active ? "Active" : "Inactive"}
      {item.brandId && ` · ${resource.data?.brands.find((brand) => brand.id === item.brandId)?.name ?? "Brand unavailable"}`}
      {allowed && <button className="button" disabled={pending || uncertain} onClick={() => { setSelected({ kind: listKind, item }); setReason(""); setError(""); }}>
        {item.active ? "Deactivate" : "Reactivate"} {item.value}
      </button>}
    </li>)}</ul>}
  </article>;
  return <section className="catalog-content"><div className="page-heading"><div><p className="eyebrow">Publication vocabulary</p><h1>Categories and collections</h1></div></div>
    <p>Online categories can be shared across brands. Collections belong to one brand. Names remain stable; replace a name by deactivating it and creating a new value.</p>
    {error && <p role="alert" className="field-error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {uncertain && <button className="button" disabled={pending} onClick={() => void run()}>Retry same catalog request</button>}
    {resource.error ? <ErrorState message="Catalog values could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading catalog…" /> : <>
      {allowed && <article className="panel"><h2>Add catalog value</h2><form onSubmit={(event) => {
        event.preventDefault();
        if (value.includes(":")) { setError("Enter one value without a colon."); return; }
        void run({ kind, create: { idempotency_key: crypto.randomUUID(), value: value.trim(), ...(kind === "collections" ? { brand_id: brand } : {}) } });
      }}><fieldset disabled={pending || uncertain || Boolean(selected)}><legend>New value</legend>
        <label>Value type<select value={kind} onChange={(event) => setKind(event.target.value as CatalogKind)}>
          <option value="online-categories">Online category</option><option value="collections">Brand collection</option></select></label>
        {kind === "collections" && <label>Collection brand<select required value={brand} onChange={(event) => setBrand(event.target.value)}>
          <option value="">Choose a brand</option>{resource.data.brands.filter((item) => item.isActive).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>}
        <label>Catalog value<input required maxLength={255} value={value} onChange={(event) => setValue(event.target.value)} /></label>
        <button className="button button--primary" disabled={!value.trim()}>Create catalog value</button>
      </fieldset></form></article>}
      {selected && <article className="panel"><h2>{selected.item.active ? "Deactivate" : "Reactivate"} {selected.item.value}</h2>
        <p>This invalidates checks and approvals for materials that use the value. Existing assignments and history remain visible.</p>
        <form onSubmit={(event) => { event.preventDefault(); void run({ kind: selected.kind, id: selected.item.id, activity: {
          idempotency_key: crypto.randomUUID(), expected_version: selected.item.version, is_active: !selected.item.active, reason: reason.trim(),
        } }); }}><fieldset disabled={pending || uncertain}><legend>Confirm availability change</legend>
          <label>Reason for catalog change<textarea required maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
          <button className="button button--primary" disabled={!reason.trim()}>Confirm availability change</button>
          <button className="button" type="button" onClick={() => setSelected(null)}>Cancel catalog change</button>
        </fieldset></form>
      </article>}
      {renderList("Online categories", "online-categories", resource.data.categories)}
      {renderList("Brand collections", "collections", resource.data.collections)}
    </>}
    <button className="button" disabled={pending || uncertain} onClick={() => { setSelected(null); resource.retry(); }}>Reload catalog</button>
  </section>;
}
