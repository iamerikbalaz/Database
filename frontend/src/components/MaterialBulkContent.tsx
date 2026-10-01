import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { catalogClient, type MaterialContent } from "../api/catalogClient";
import { materialBulkContentClient, type BulkContentAdd } from "../api/materialCreationClient";
import type { Material } from "../api/materialDto";
import { ApiError } from "../api/errors";
import { useResource } from "../api/useResource";
import { useNavigationGuard } from "../navigationGuard";
import "./MaterialLayout.css";

function BulkContentDialog({ materials, onClose, onSaved, onBusyChange }: {
  materials: Material[]; onClose: () => void; onSaved: () => void; onBusyChange: (value: boolean) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [categoryIds, setCategories] = useState<string[]>([]), [collectionIds, setCollections] = useState<string[]>([]);
  const [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false), [error, setError] = useState("");
  const pending = useRef<BulkContentAdd | null>(null), sending = useRef(false);
  useNavigationGuard(() => sending.current || pending.current !== null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  const sameCustomer = new Set(materials.map(item => item.publishedBrandId)).size === 1;
  const load = useCallback(async () => {
    const [categories, collections, contents] = await Promise.all([catalogClient.categories(), sameCustomer ? catalogClient.collections(materials[0].publishedBrandId) : Promise.resolve([]), Promise.all(materials.map(item => catalogClient.content(item.id)))]);
    return { categories, collections, contents };
  }, [materials, sameCustomer]);
  const loaded = useResource(load);
  const toggle = (items: string[], id: string, selected: boolean) => selected ? [...items, id] : items.filter(item => item !== id);
  const save = async () => {
    if (sending.current || !loaded.data) return;
    const contents = new Map<string, MaterialContent>(loaded.data.contents.map(item => [item.materialId, item]));
    pending.current ??= { idempotency_key: crypto.randomUUID(), materials: materials.map(item => ({ id: item.id, expected_updated_at: item.updatedAt, expected_revision: contents.get(item.id)!.revision })), category_ids: categoryIds, collection_ids: collectionIds };
    sending.current = true; setBusy(true); setError("");
    try {
      await materialBulkContentClient.add(pending.current);
      pending.current = null; setUncertain(false); onSaved(); onClose();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) { pending.current = null; setUncertain(false); setError(cause.message); }
      else { setUncertain(true); setError("The result is unknown. Recover this same request before changing the selection."); }
    } finally { sending.current = false; setBusy(false); }
  };
  const close = () => { if (!sending.current && !pending.current) onClose(); };
  useEffect(() => { onBusyChange(true); return () => onBusyChange(false); }, [onBusyChange]);
  return <dialog ref={dialog} className="material-name-dialog material-bulk-content-dialog" aria-label="Add categories and collections" onCancel={event => { if (sending.current || pending.current) event.preventDefault(); else onClose(); }}>
    <h2>Add categories and collections</h2><p>Add to {materials.length} selected materials. Existing categories, Main category and collections remain selected.</p>
    {error && <p role="alert" className="form-error">{error}</p>}
    {loaded.error ? <p role="alert">Catalog or selected material content could not be loaded. <button onClick={loaded.retry}>Retry</button></p> : !loaded.data ? <p>Loading selected material content…</p> : <div className="material-bulk-content-columns">
      <fieldset disabled={busy || uncertain}><legend>Categories</legend><div className="material-choice-grid">{loaded.data.categories.filter(item => item.active).map(item => <label className="checkbox-label" key={item.id}><input type="checkbox" checked={categoryIds.includes(item.id)} onChange={event => setCategories(toggle(categoryIds, item.id, event.target.checked))} />{item.abbreviation ? item.abbreviation + " · " : ""}{item.value}</label>)}</div></fieldset>
      <details className="material-choice-group"><summary>Brand collections <span>{collectionIds.length} selected</span></summary><fieldset disabled={busy || uncertain || !sameCustomer}><legend className="sr-only">Brand collections</legend><div className="material-choice-grid">{!sameCustomer ? <p>Collections require all selected materials to belong to the same Customer.</p> : loaded.data.collections.filter(item => item.active).map(item => <label className="checkbox-label" key={item.id}><input type="checkbox" checked={collectionIds.includes(item.id)} onChange={event => setCollections(toggle(collectionIds, item.id, event.target.checked))} />{item.value}</label>)}</div></fieldset></details>
    </div>}
    <div className="form-actions"><button className="button button--primary" disabled={busy || !loaded.data || (!uncertain && !categoryIds.length && !collectionIds.length)} onClick={() => void save()}>{busy ? "Applying…" : uncertain ? "Recover bulk content save" : "Add to selected materials"}</button>
      <button className="button" disabled={busy || uncertain} onClick={close}>Cancel</button></div>
  </dialog>;
}

export function MaterialBulkContent({ materials, actionTarget, dockOnly = false, disabled, onChanged, onBusyChange }: {
  materials: Material[]; actionTarget: HTMLElement | null; dockOnly?: boolean; disabled?: boolean; onChanged: () => void; onBusyChange: (value: boolean) => void;
}) {
  const [selection, setSelection] = useState<Material[] | null>(null);
  const button = <button className="button" disabled={disabled || !materials.length || materials.length > 100} onClick={() => setSelection(materials.map(item => ({ ...item })))}>Categories / collections</button>;
  return <>{materials.length > 0 && (actionTarget ? createPortal(button, actionTarget) : dockOnly ? null : button)}{selection && <BulkContentDialog materials={selection} onClose={() => setSelection(null)} onSaved={onChanged} onBusyChange={onBusyChange} />}</>;
}
