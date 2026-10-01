import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { catalogClient, type CatalogValue, type ContentPayload, type MaterialContent } from "../api/catalogClient";
import { ApiError } from "../api/errors";
import type { Material } from "../api/materialDto";
import { metadataClient, type MetadataObservation, type MetadataOperation, type MetadataSave } from "../api/metadataClient";
import { materialLocalClient } from "../api/materialLocalClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { MaterialColorSelect } from "./MaterialColorSelect";
import { useNavigationGuard } from "../navigationGuard";
import { ErrorState, LoadingState } from "./PageState";
import { Icon } from "./Icon";
import "./MaterialLayout.css";

const vocabularyLabel = (item: CatalogValue) => item.abbreviation ? `${item.abbreviation} · ${item.value}` : item.value;

function ContentEditor({ material, content, categories, collections, source, onSaved, reload, onBusyChange, disabled, headingActions }: {
  material: Material; content: MaterialContent; categories: CatalogValue[]; collections: CatalogValue[]; source: MetadataObservation;
  onSaved: () => void | Promise<unknown>; reload: () => void; onBusyChange?: (busy: boolean) => void; disabled?: boolean;
  headingActions: HTMLElement | null;
}) {
  const role = useSession()?.session.user.role;
  const allowed = role === "ADMIN" || role === "PRODUCTION_LEAD" || role === "PROCESSOR";
  const [description, setDescription] = useState(content.description ?? "");
  const [credits, setCredits] = useState(content.credits?.toString() ?? "");
  const [tags, setTags] = useState(content.tags.join("\n"));
  const required = content.requiredCategoryId;
  const [selectedCategories, setCategories] = useState([...new Set([...content.categories.map(item => item.id), ...(required ? [required] : [])])]);
  const [selectedCollections, setCollections] = useState(content.collections.map(item => item.id));
  const [color, setColor] = useState(source.values.hex_color ?? "");
  const [width, setWidth] = useState(source.values.width_cm ?? ""), [height, setHeight] = useState(source.values.height_cm ?? "");
  const [widthEdited, setWidthEdited] = useState(false), [heightEdited, setHeightEdited] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false), [uncertain, setUncertain] = useState(false), [active, setActive] = useState<MetadataOperation | null>(source.active);
  const payload = useRef<ContentPayload | null>(null), metadataPayload = useRef<MetadataSave | null>(null), sending = useRef(false);
  const busy = pending || uncertain || active !== null;
  const sameIds = (left: string[], right: string[]) => left.length === right.length && left.every(id => right.includes(id));
  const dirty = description !== (content.description ?? "") || credits !== (content.credits?.toString() ?? "") || tags !== content.tags.join("\n") ||
    color !== (source.values.hex_color ?? "") || width !== (source.values.width_cm ?? "") || height !== (source.values.height_cm ?? "") ||
    !sameIds(selectedCategories, [...new Set([...content.categories.map(item => item.id), ...(required ? [required] : [])])]) ||
    !sameIds(selectedCollections, content.collections.map(item => item.id));
  useEffect(() => { onBusyChange?.(busy || dirty); return () => onBusyChange?.(false); }, [busy, dirty, onBusyChange]);
  useNavigationGuard(() => dirty || sending.current || payload.current !== null || metadataPayload.current !== null || active?.status === "RUNNING");
  useEffect(() => {
    const prevent = (event: BeforeUnloadEvent) => { if (dirty || busy) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty, busy]);
  const canWriteSource = allowed && source.available && source.writesEnabled && source.editable;
  const sourceReady = canWriteSource && !busy && !disabled;
  const categoryChoices = [...categories];
  for (const item of content.categories) if (!categoryChoices.some(choice => choice.id === item.id)) categoryChoices.push(item);
  const validSize = (value: string, edited: boolean) => value === "" || /^(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,4})?$/.test(value) && Number(value) > 0 && (!edited || /^\d+(?:\.\d)?$/.test(value));
  const save = async () => {
    if (sending.current || disabled) return;
    const tagValues = tags.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
    if (!payload.current && !active && (tagValues.length > 100 || tagValues.some(item => item.includes(":") || item.length > 100) ||
      (credits !== "" && (!/^\d+$/.test(credits) || Number(credits) > 2147483647)))) {
      setError("Use at most 100 individual tags without colons, up to 100 characters each, and nonnegative whole-number credits."); return;
    }
    if (!metadataPayload.current && !active && (!validSize(width, widthEdited) || !validSize(height, heightEdited))) {
      setError("Enter positive sample sizes with one decimal place, or leave a value blank."); return;
    }
    sending.current = true; setPending(true); setError(""); setNotice("");
    try {
      if (!active) payload.current ??= { idempotency_key: crypto.randomUUID(), expected_revision: content.revision,
        description: description.trim() || null, credits: credits === "" ? null : Number(credits), tags: tagValues,
        category_ids: [...new Set([...selectedCategories, ...(required ? [required] : [])])], collection_ids: selectedCollections };
      if (canWriteSource || metadataPayload.current || active) {
        if (!active) metadataPayload.current ??= { idempotency_key: crypto.randomUUID(), expected_updated_at: source.expectedUpdatedAt,
          expected_sha256: source.sha256, values: { hex_color: color || null, width_cm: width || null, height_cm: height || null }, content: payload.current! };
        const result = active ? await metadataClient.resume(material.id, active.id) : await metadataClient.save(material.id, metadataPayload.current!);
        if (result.status === "RUNNING") { setActive(result); setUncertain(true); setError("The save is pending verification. Recover the recorded save before making more changes."); return; }
        if (result.status !== "COMPLETED") {
          payload.current = null; metadataPayload.current = null; setActive(null); setUncertain(false);
          setError("The source or material changed. Reload source and review the values before saving again."); return;
        }
      } else await catalogClient.save(material.id, payload.current!);
      payload.current = null; metadataPayload.current = null; setActive(null); setUncertain(false);
      setNotice(canWriteSource ? "Library data and metadata.json saved." : "Library content saved. Source metadata was not edited.");
      await onSaved(); reload();
    } catch (cause) {
      if (!active && cause instanceof ApiError && (cause.status >= 400 && cause.status < 500 || cause.code === "SOURCE_MUTATIONS_DISABLED" || cause.code === "SOURCE_METADATA_UNAVAILABLE")) {
        payload.current = null; metadataPayload.current = null; setUncertain(false);
        setError("The data could not be saved. Reload source and review permissions, the current revision and active catalog values.");
      } else { setUncertain(true); setError("The save outcome is unknown. Recover the same request before making more changes."); }
    } finally { sending.current = false; setPending(false); }
  };
  const openMetadata = async () => {
    if (sending.current) return;
    sending.current = true; setPending(true); setError("");
    try { await materialLocalClient.openMetadata(material.id); }
    catch { setError("metadata.json could not be opened. Save the library data to create a missing file, and check the local desktop connection."); }
    finally { sending.current = false; setPending(false); }
  };
  const choices = (label: string, items: CatalogValue[], selected: string[], change: (value: string[]) => void) => <details className="material-choice-group">
    <summary>{label} <span>{items.filter(item => item.id === required || selected.includes(item.id)).length} selected</span></summary><div className="material-choice-grid">{items.length === 0 && <p>No values available.</p>}
    {items.map(item => <label key={item.id} className="checkbox-label"><input type="checkbox" checked={item.id === required || selected.includes(item.id)}
      disabled={!allowed || busy || item.id === required || (!item.active && !selected.includes(item.id))}
      onChange={event => change(event.target.checked ? [...selected, item.id] : selected.filter(id => id !== item.id))} />
      {vocabularyLabel(item)}{item.id === required ? " (Main category)" : !item.active ? " (inactive; remove before saving)" : ""}</label>)}
  </div></details>;
  return <>
    {headingActions && createPortal(<button className="button material-icon-button" type="button" aria-label="Reload source" title="Reload source" disabled={busy || disabled || dirty} onClick={reload}><Icon name="refresh" size={18} /></button>, headingActions)}
    <p>Revision {content.revision} · {content.status === "EMPTY" ? "Empty" : "Saved content"}</p>
    {error && <p role="alert" className="field-error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {dirty && !busy && <p role="status">Unsaved library changes. Save or discard these changes before editing the material properties or leaving this card.</p>}
    {!source.available && <p role="status">Source unavailable. Color and size show their last recorded values.</p>}
    {source.available && !source.writesEnabled && <p role="status">Source file editing is disabled in this environment.</p>}
    {source.available && !source.editable && <p role="status">The current metadata file cannot be edited safely.</p>}
    {source.sourceStatus === "MISSING" && canWriteSource && <p>Saving creates the missing metadata.json from these recorded values.</p>}
    <form onSubmit={event => { event.preventDefault(); void save(); }} noValidate>
      <fieldset className="material-library-fields" disabled={!allowed || busy || disabled}><legend className="sr-only">Material data for library</legend>
        <div className="material-library-text">
          <label>Description<textarea rows={3} maxLength={10000} value={description} onChange={event => setDescription(event.target.value)} /></label>
          <label>Tags, one per line<textarea rows={3} value={tags} onChange={event => setTags(event.target.value)} /></label>
        </div>
        <div className="material-library-metrics">
          <MaterialColorSelect value={color} disabled={!sourceReady} onChange={setColor} />
          <label>Sample width (cm)<input aria-label="Sample width (cm)" type="number" min="0.1" max="99999999.9" step="0.1" value={width} disabled={!sourceReady}
            onChange={event => { setWidth(event.target.value); setWidthEdited(true); }} /></label>
          <label>Sample height (cm)<input aria-label="Sample height (cm)" type="number" min="0.1" max="99999999.9" step="0.1" value={height} disabled={!sourceReady}
            onChange={event => { setHeight(event.target.value); setHeightEdited(true); }} /></label>
          <label>Credits<input type="number" min="0" max="2147483647" step="1" value={credits} onChange={event => setCredits(event.target.value)} /></label>
        </div>
        <div className="material-library-choices">
        {choices("Online categories", categoryChoices, selectedCategories, setCategories)}
        {choices("Brand collections", collections, selectedCollections, setCollections)}
        </div>
        {(role === "ADMIN" || role === "PRODUCTION_LEAD") && <p><a href="/catalog">Manage categories and brand collections</a></p>}
      </fieldset>
      <div className="form-actions">
        {allowed && <button className="button button--primary" disabled={pending || disabled} type="submit">{uncertain || active ? "Recover library data save" : pending ? "Saving…" : "Save library data"}</button>}
        <button className="button" type="button" disabled={busy || disabled} onClick={() => void openMetadata()}>Open metadata.json</button>
        {dirty && <button className="button" type="button" disabled={busy || disabled} onClick={reload}>Discard changes</button>}
      </div>
    </form>
  </>;
}

export function MaterialContentPanel({ material, onChanged, onBusyChange, disabled }: { material: Material; onChanged: () => void | Promise<unknown>; onBusyChange?: (busy: boolean) => void; disabled?: boolean }) {
  const actor = useSession()?.session.user.id;
  const [headingActions, setHeadingActions] = useState<HTMLDivElement | null>(null);
  const load = useCallback(async () => {
    void actor; void material.mainCategoryCode; void material.updatedAt;
    const [content, categories, collections, source] = await Promise.all([catalogClient.content(material.id), catalogClient.categories(), catalogClient.collections(material.publishedBrandId), metadataClient.inspect(material.id)]);
    return { content, categories, collections, source };
  }, [material.id, material.publishedBrandId, material.mainCategoryCode, material.updatedAt, actor]);
  const resource = useResource(load);
  return <article className="panel catalog-content material-library-panel" aria-label="Publication content"><div className="material-section-heading"><h2>Material data for library</h2><div ref={setHeadingActions}>
    {!resource.data && <button className="button material-icon-button" type="button" aria-label="Reload source" title="Reload source" disabled={!resource.error || disabled} onClick={resource.retry}><Icon name="refresh" size={18} /></button>}
  </div></div>
    {resource.error ? <ErrorState message="Library data could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading library data…" /> :
      <ContentEditor key={`${material.mainCategoryCode}:${resource.data.content.revision}:${resource.data.source.expectedUpdatedAt}:${resource.data.source.sha256}`} material={material}
        {...resource.data} onSaved={onChanged} onBusyChange={onBusyChange} disabled={disabled} reload={resource.retry} headingActions={headingActions} />}
  </article>;
}
