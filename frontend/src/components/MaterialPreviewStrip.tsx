import { useEffect, useRef, useState } from "react";
import type { Material } from "../api/materialDto";
import { GalleryStore, orderedGalleryEntries } from "../api/galleryStore";
import type { PreviewEntry } from "../api/previewClient";
import { Icon } from "./Icon";
import { PreviewLightbox } from "./PreviewLightbox";
import "./MaterialPreviews.css";

function PreviewImage({ material, entry, store }: { material: Material; entry: PreviewEntry; store: GalleryStore }) {
  const target = useRef<HTMLDivElement>(null), [near, setNear] = useState(false), [url, setUrl] = useState(""), [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!target.current) return;
    if (typeof IntersectionObserver === "undefined") { const timer = setTimeout(() => setNear(true), 0); return () => clearTimeout(timer); }
    const observer = new IntersectionObserver(entries => { if (entries.some(item => item.isIntersecting)) { setNear(true); observer.disconnect(); } }, { rootMargin: "180px" });
    observer.observe(target.current); return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!near) return;
    const controller = new AbortController(); let live = true, objectUrl = "";
    void store.image(material.id, material.folderPath!, entry, 256, controller.signal).then(value => {
      if (live) { objectUrl = URL.createObjectURL(value.blob); setUrl(objectUrl); }
    }, () => { if (live) setFailed(true); });
    return () => { live = false; controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [material.id, material.folderPath, entry, store, near]);
  return <div ref={target} className="material-strip-image">{url && !failed ? <img src={url} alt={`${material.materialName} — ${entry.name}`} width={112} height={112} decoding="async" onError={() => setFailed(true)} /> : <span>{failed ? "Unavailable" : "…"}</span>}</div>;
}

export function MaterialPreviewStrip({ material, store, editable, onEdit, onCount }: {
  material: Material; store: GalleryStore; editable: boolean;
  onEdit: (action: "RENAME" | "DELETE", filename: string) => void; onCount: (materialId: string, count: number) => void;
}) {
  const target = useRef<HTMLDivElement>(null), [near, setNear] = useState(false);
  const [entries, setEntries] = useState<PreviewEntry[] | null>(null), [error, setError] = useState(false), [attempt, setAttempt] = useState(0);
  const [openedName, setOpenedName] = useState<string | null>(null);
  useEffect(() => {
    if (!target.current) return;
    if (typeof IntersectionObserver === "undefined") { const timer = setTimeout(() => setNear(true), 0); return () => clearTimeout(timer); }
    const observer = new IntersectionObserver(items => { if (items.some(item => item.isIntersecting)) { setNear(true); observer.disconnect(); } }, { rootMargin: "150px" });
    observer.observe(target.current); return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!near || !material.folderPath) return;
    let live = true; const controller = new AbortController();
    void store.listing(material.id, material.folderPath, controller.signal).then(value => {
      if (live) { const next = orderedGalleryEntries(value.items); setEntries(next); onCount(material.id, next.length); }
    }, () => { if (live) setError(true); });
    return () => { live = false; controller.abort(); };
  }, [near, material.id, material.folderPath, store, onCount, attempt]);
  return <div ref={target} className="material-preview-strip" aria-label={`All previews for ${material.materialName}`}>
    {!material.folderPath ? <span>No folder linked</span> : error ? <button className="button" onClick={() => { store.forget(material.id, material.folderPath!); setError(false); setAttempt(value => value + 1); }}>Reload previews</button> : entries?.length === 0 ? <span>No PNG previews</span> : !entries ? <span>Loading previews…</span> : entries.map(entry => <figure key={`${entry.name}:${entry.sha256}`}>
      <button type="button" className="material-strip-open" aria-label={`Open ${entry.name} from ${material.materialName} in full quality`} onClick={event => { event.stopPropagation(); setOpenedName(entry.name); }}><PreviewImage material={material} entry={entry} store={store} /></button>
      {editable && <button type="button" className="preview-delete" aria-label={`Delete ${entry.name} from ${material.materialName}`} title="Delete preview" onClick={() => onEdit("DELETE", entry.name)}><Icon name="trash" size={14} /></button>}
      <figcaption>{editable ? <button type="button" className="preview-filename" title="Double-click to rename; Enter to edit" onDoubleClick={() => onEdit("RENAME", entry.name)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onEdit("RENAME", entry.name); } }}>{entry.name}</button> : <span className="preview-filename">{entry.name}</span>}</figcaption>
    </figure>)}
    {openedName !== null && entries && <PreviewLightbox materialId={material.id} entries={entries} selectedName={openedName} onClose={() => setOpenedName(null)} />}
  </div>;
}
