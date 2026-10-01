import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import { catalogClient } from "../api/catalogClient";
import { directoryClient } from "../api/directoryClient";
import { materialCreationClient, type MaterialBatchCreate, type MaterialCreationResult } from "../api/materialCreationClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { categoryLabel, catalogMaterialCategories } from "../data/materialCategories";
import { ErrorState, LoadingState } from "../components/PageState";
import { useNavigationGuard } from "../navigationGuard";
import { pastedMaterialNames } from "../forms/materialNames";

export function MaterialCreatePage({ client, navigate }: { client: ApiClient; navigate: (path: string) => void }) {
  const actor = useSession()?.session.user.id ?? "current";
  const storageKey = "reawote.material-create." + actor;
  const [pending, setPending] = useState<MaterialBatchCreate | null>(() => {
    try { const raw = sessionStorage.getItem(storageKey); return raw ? JSON.parse(raw) as MaterialBatchCreate : null; } catch { return null; }
  });
  const [multiple, setMultiple] = useState(Boolean(pending && pending.names.length > 1));
  const [names, setNames] = useState(pending?.names.join("\n") ?? "");
  const [orderId, setOrder] = useState(pending?.project_id ?? ""), [customerId, setCustomer] = useState(pending?.published_brand_id ?? "");
  const [processorId, setProcessor] = useState(pending?.assigned_processor_id ?? ""), [category, setCategory] = useState(pending?.main_category_code ?? "");
  const [categoryIds, setCategoryIds] = useState<string[]>(pending?.category_ids ?? []), [collectionIds, setCollectionIds] = useState<string[]>(pending?.collection_ids ?? []);
  const [resolution, setResolution] = useState(String(pending?.resolution ?? 8)), [template, setTemplate] = useState(pending?.template_name ?? "");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [receipt, setReceipt] = useState<MaterialCreationResult | null>(null);
  const sending = useRef(false), uncertain = useRef(Boolean(pending));
  const errorRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useNavigationGuard(() => sending.current || uncertain.current);
  const load = useCallback(async () => {
    const [customers, orders, processors, categories, collections, options] = await Promise.all([
      directoryClient.customers(), directoryClient.orders(), client.getInternalUsers(true), catalogClient.categories(), catalogClient.collections(), materialCreationClient.options(),
    ]);
    return { customers, orders, processors, categories, collections, options };
  }, [client]);
  const loaded = useResource(load);
  function remember(value: MaterialBatchCreate | null) {
    setPending(value);
    try { if (value) sessionStorage.setItem(storageKey, JSON.stringify(value)); else sessionStorage.removeItem(storageKey); } catch { /* Same-request recovery still works while this page remains open. */ }
  }
  const save = async () => {
    if (sending.current || (!pending && !loaded.data)) return;
    let payload = pending;
    if (!payload) {
      if (!loaded.data) return;
      try {
        const values = pastedMaterialNames(names);
        if (!multiple && values.length !== 1) throw new Error("Enable Create multiple materials to use more than one name.");
        if (!customerId || !processorId || !category || !template || !/^\d+$/.test(resolution) || Number(resolution) < 1 || Number(resolution) > 32) throw new Error("Choose Customer, Processor, Main category, SBS template and a whole-number resolution from 1 to 32K.");
        payload = { idempotency_key: crypto.randomUUID(), expected_paths_version: loaded.data.options.pathsVersion, project_id: orderId || null,
          published_brand_id: customerId, assigned_processor_id: processorId, main_category_code: category,
          names: values, category_ids: categoryIds, collection_ids: collectionIds, resolution: Number(resolution), template_name: template };
        remember(payload);
      } catch (cause) { setError(cause instanceof Error ? cause.message : "Review the material names."); return; }
    }
    sending.current = true; uncertain.current = true; setBusy(true); setError("");
    try {
      const result = await materialCreationClient.create(payload);
      setReceipt(result); uncertain.current = false;
      if (result.status === "COMPLETED") remember(null);
      else setError("Some folders remain unfinished. Their material records and numbers are reserved. Retry this same batch to finish them.");
    } catch (cause) {
      setError(cause instanceof ApiError ? [cause.message, ...cause.issues.map(item => item.message)].join(" ") : "The result is unknown. Recover the same request to avoid duplicate materials.");
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        try { setReceipt(await materialCreationClient.lookup(payload.idempotency_key)); uncertain.current = false; }
        catch (lookup) { if (lookup instanceof ApiError && lookup.status === 404) { remember(null); uncertain.current = false; } }
      }
    } finally { sending.current = false; setBusy(false); }
  };
  const results = receipt && <section aria-label="Material creation results"><h2>{receipt.completedCount} of {receipt.totalCount} folders created</h2><p>Existing records and folders are never overwritten. Retries reuse these material numbers.</p><ul>{receipt.items.map(item => <li key={item.materialId}><a href={"/materials/" + item.materialId}>{item.identity}</a> — {item.status}{item.errorCode ? ": " + item.errorCode : ""}</li>)}</ul></section>;
  if (loaded.error && (pending || receipt)) return <section className="panel"><h1>Recover material creation</h1>
    <p>Current creation options are unavailable. Recovery uses the stored request and its original template copy.</p>
    {error && <p role="alert" className="form-error">{error}</p>}{results}
    <div className="form-actions">{pending && <button className="button button--primary" disabled={busy} onClick={() => void save()}>{busy ? "Recovering…" : "Recover / finish this batch"}</button>}
      <button className="button" disabled={busy || receipt === null} onClick={() => navigate("/materials")}>Back to materials</button></div>
  </section>;
  if (loaded.error) return <ErrorState message="Material creation options could not be loaded. Check desktop connection and the SBS template folder in Settings." retry={loaded.retry} />;
  if (!loaded.data) return <LoadingState label="Loading material creation…" />;
  const data = loaded.data;
  const mainCategories = catalogMaterialCategories(data.categories).filter(item => /^[A-Z0-9-]+$/.test(item.code));
  const selectedOrder = data.orders.find(item => item.id === orderId);
  const frozen = busy || pending !== null || receipt?.status === "COMPLETED";
  const toggle = (items: string[], id: string, selected: boolean) => selected ? [...items, id] : items.filter(value => value !== id);
  return <section className="panel panel--wide"><h1>Add material</h1>
    <p>New materials use uppercase names and unique Customer numbers. Their folders contain the chosen resolution, PREVIEW and SOURCE with a copy of the selected SBS template.</p>
    {error && <p role="alert" tabIndex={-1} ref={errorRef} className="form-error">{error}</p>}
    <form aria-label="Add material" noValidate onSubmit={event => { event.preventDefault(); void save(); }}>
      <fieldset disabled={frozen}><legend>Shared material properties</legend><div className="form-grid">
        <label>Order<select autoFocus value={orderId} onChange={event => { const id = event.target.value; setOrder(id); const order = data.orders.find(item => item.id === id); if (order?.customerId) { setCustomer(order.customerId); setCollectionIds([]); } }}><option value="">No order</option>{data.orders.map(item => <option key={item.id} value={item.id}>{item.generatedName}</option>)}</select></label>
        <label>Customer<select required value={customerId} disabled={Boolean(selectedOrder?.customerId)} onChange={event => { setCustomer(event.target.value); setCollectionIds([]); }}><option value="">Choose Customer</option>{data.customers.filter(item => item.isActive).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label>Main category<select required value={category} onChange={event => setCategory(event.target.value)}><option value="">Choose category</option>{mainCategories.map(item => <option key={item.code} value={item.code}>{categoryLabel(item.code, mainCategories)}</option>)}</select></label>
        <label>Processor<select required value={processorId} onChange={event => setProcessor(event.target.value)}><option value="">Choose Processor</option>{data.processors.filter(item => item.isActive && item.role === "PROCESSOR").map(item => <option key={item.id} value={item.id}>{item.displayName}</option>)}</select></label>
        <label>Resolution (K)<input type="number" min="1" max="32" step="1" value={resolution} required onChange={event => setResolution(event.target.value)} /></label>
        <label>SBS template<select required value={template} onChange={event => setTemplate(event.target.value)}><option value="">Choose template</option>{data.options.templates.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label>
      </div>
      <label className="checkbox-label"><input type="checkbox" checked={multiple} onChange={event => setMultiple(event.target.checked)} />Create multiple materials</label>
      {multiple ? <label>Material names — one Excel column<textarea rows={8} required value={names} onChange={event => setNames(event.target.value)} placeholder="FIRST MATERIAL&#10;SECOND MATERIAL" /></label>
        : <label>Material name<input required maxLength={255} value={names} onChange={event => setNames(event.target.value)} /></label>}
      <fieldset><legend>Additional categories</legend>{data.categories.filter(item => item.active).map(item => <label className="checkbox-label" key={item.id}><input type="checkbox" checked={item.abbreviation === category || categoryIds.includes(item.id)} disabled={item.abbreviation === category}
        onChange={event => setCategoryIds(toggle(categoryIds, item.id, event.target.checked))} />{item.abbreviation ? item.abbreviation + " · " : ""}{item.value}{item.abbreviation === category ? " (Main category)" : ""}</label>)}</fieldset>
      <fieldset><legend>Brand collections</legend>{data.collections.filter(item => item.active && item.brandId === customerId).map(item => <label className="checkbox-label" key={item.id}><input type="checkbox" checked={collectionIds.includes(item.id)}
        onChange={event => setCollectionIds(toggle(collectionIds, item.id, event.target.checked))} />{item.value}</label>)}{!customerId && <p>Choose a Customer first.</p>}</fieldset>
      </fieldset>
      <div className="form-actions">{receipt?.status !== "COMPLETED" && <button type="submit" className="button button--primary" disabled={busy || (!pending && !data.options.templates.length)}>{busy ? "Creating…" : pending ? "Recover / finish this batch" : multiple ? "Create materials" : "Create material"}</button>}
      <button className="button" type="button" disabled={busy || (pending !== null && receipt === null)} onClick={() => navigate("/materials")}>Back to materials</button></div>
    </form>
    {results}
  </section>;
}
