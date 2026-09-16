import { useCallback, useRef, useState } from "react";
import { catalogClient, type CatalogValue, type ContentPayload, type MaterialContent } from "../api/catalogClient";
import { ApiError } from "../api/errors";
import type { Material } from "../api/materialDto";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ErrorState, LoadingState } from "./PageState";

function ContentEditor({ content, categories, collections, onSaved, reload }: {
  content: MaterialContent; categories: CatalogValue[]; collections: CatalogValue[]; onSaved: () => void; reload: () => void;
}) {
  const role = useSession()?.session.user.role;
  const allowed = role === "ADMIN" || role === "PRODUCTION_LEAD" || role === "PROCESSOR";
  const [description, setDescription] = useState(content.description ?? "");
  const [credits, setCredits] = useState(content.credits?.toString() ?? "");
  const [tags, setTags] = useState(content.tags.join("\n"));
  const [selectedCategories, setCategories] = useState(content.categories.map((item) => item.id));
  const [selectedCollections, setCollections] = useState(content.collections.map((item) => item.id));
  const [reason, setReason] = useState("");
  const [error, setError] = useState(""); const [pending, setPending] = useState(false); const [uncertain, setUncertain] = useState(false);
  const payload = useRef<ContentPayload | null>(null); const sending = useRef(false);
  const [history, setHistory] = useState<Awaited<ReturnType<typeof catalogClient.history>> | null>(null);
  const [historyError, setHistoryError] = useState(false);
  const save = async () => {
    if (sending.current) return;
    const tagValues = tags.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    if (!payload.current && (tagValues.length > 100 || tagValues.some((item) => item.includes(":") || item.length > 100) ||
        (credits !== "" && (!/^\d+$/.test(credits) || Number(credits) > 2147483647)))) {
      setError("Use at most 100 individual tags without colons, up to 100 characters each, and nonnegative whole-number credits."); return;
    }
    sending.current = true; setPending(true); setError("");
    payload.current ??= { idempotency_key: crypto.randomUUID(), expected_revision: content.revision,
      description: description.trim() || null, credits: credits === "" ? null : Number(credits), tags: tagValues,
      category_ids: selectedCategories, collection_ids: selectedCollections, reason: reason.trim() };
    try {
      await catalogClient.save(content.materialId, payload.current);
      payload.current = null; setUncertain(false); reload(); onSaved();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        payload.current = null; setUncertain(false);
        setError("The draft could not be saved. Check your role and selected active catalog values, then reload if another person changed the material.");
      } else {
        setUncertain(true); setError("The save outcome is unknown. Retry the same request before making more changes.");
      }
    } finally { sending.current = false; setPending(false); }
  };
  const choices = (label: string, items: CatalogValue[], selected: string[], change: (value: string[]) => void) => <fieldset>
    <legend>{label}</legend>{items.length === 0 && <p>No values available.</p>}
    {items.map((item) => <label key={item.id}><input type="checkbox" checked={selected.includes(item.id)} disabled={!item.active && !selected.includes(item.id)}
      onChange={(event) => change(event.target.checked ? [...selected, item.id] : selected.filter((id) => id !== item.id))} />{item.value}{!item.active && " (inactive; remove before saving)"}</label>)}
  </fieldset>;
  return <>
    <p>Revision {content.revision} · {content.status === "EMPTY" ? "Empty" : "Manual draft"}. Saving a change invalidates technical checks and publication approvals.</p>
    {error && <p role="alert" className="field-error">{error}</p>}
    {allowed ? <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <fieldset disabled={pending || uncertain}><legend>Publication draft</legend>
        <label>Description<textarea maxLength={10000} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        <label>Credits<input type="number" min="0" max="2147483647" step="1" value={credits} onChange={(event) => setCredits(event.target.value)} /></label>
        <label>Tags, one per line<textarea value={tags} onChange={(event) => setTags(event.target.value)} /></label>
        <p>Categories and tags remain individual values. Colons are reserved for publication export.</p>
        {choices("Online categories", categories, selectedCategories, setCategories)}
        {choices("Brand collections", collections, selectedCollections, setCollections)}
        <label>Reason for content change<textarea required maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      </fieldset>
      <button className="button button--primary" disabled={pending || !reason.trim()}>{uncertain ? "Retry same content save" : "Save publication draft"}</button>
    </form> : <dl><dt>Description</dt><dd>{description || "Not entered"}</dd><dt>Credits</dt><dd>{credits || "Not entered"}</dd>
      <dt>Tags</dt><dd>{content.tags.join(", ") || "None"}</dd><dt>Online categories</dt><dd>{content.categories.map((item) => item.value).join(", ") || "None"}</dd>
      <dt>Brand collections</dt><dd>{content.collections.map((item) => item.value).join(", ") || "None"}</dd></dl>}
    <button className="button" disabled={pending || uncertain} onClick={reload}>Reload content and discard local edits</button>
    {(role === "ADMIN" || role === "PRODUCTION_LEAD") && <p><a href="/catalog">Manage categories and brand collections</a></p>}
    <details><summary>Content history</summary>
      <button className="button" onClick={() => { setHistoryError(false); void catalogClient.history(content.materialId).then(setHistory, () => setHistoryError(true)); }}>Load content history</button>
      {historyError && <p role="alert">Content history could not be loaded.</p>}
      {history && <ul>{history.map((item) => <li key={item.id}><strong>Revision {item.revision}</strong> · {item.reason} · <time dateTime={item.createdAt}>{item.createdAt}</time>
        <details><summary>Saved content</summary><p>{item.snapshot.description || "No description"}</p><p>Credits: {item.snapshot.credits ?? "Not entered"}</p>
          <p>Tags: {item.snapshot.tags.join(", ") || "None"}</p><p>Categories: {item.snapshot.categories.map((value) => value.value).join(", ") || "None"}</p>
          <p>Collections: {item.snapshot.collections.map((value) => value.value).join(", ") || "None"}</p></details></li>)}</ul>}
    </details>
  </>;
}

export function MaterialContentPanel({ material, onChanged }: { material: Material; onChanged: () => void }) {
  const load = useCallback(async () => {
    const [content, categories, collections] = await Promise.all([catalogClient.content(material.id), catalogClient.categories(), catalogClient.collections(material.publishedBrandId)]);
    return { content, categories, collections };
  }, [material.id, material.publishedBrandId]);
  const resource = useResource(load);
  return <article className="panel catalog-content" aria-label="Publication content"><h2>Publication content</h2>
    <p>Prepare descriptions, credits, online categories, tags and brand collections. Content is saved as a draft.</p>
    {resource.error ? <ErrorState message="Publication content could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading publication content…" /> :
      <ContentEditor key={resource.data.content.revision} {...resource.data} onSaved={onChanged} reload={resource.retry} />}
  </article>;
}
