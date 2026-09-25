import { useCallback, useEffect, useRef, useState } from "react";
import type { Material } from "../api/materialDto";
import { GalleryStore, orderedGalleryEntries } from "../api/galleryStore";
import type { PreviewEntry } from "../api/previewClient";
import { NavigationLink } from "./NavigationLink";

export type GallerySize = "small" | "medium" | "large" | "extra-large";

function TileImage({ material, entry, store, size, failed }: { material: Material; entry: PreviewEntry; store: GalleryStore; size: 256 | 512; failed: () => void }) {
  const [url, setUrl] = useState<string>();
  useEffect(() => {
    const controller = new AbortController(); let live = true; let settled = false; let objectUrl: string | undefined;
    void Promise.resolve().then(() => live ? store.image(material.id, material.folderPath!, entry, size, controller.signal) : undefined).then(value => {
      settled = true; if (!live || !value) return;
      objectUrl = URL.createObjectURL(value.blob); setUrl(objectUrl);
    }, () => { settled = true; if (live) failed(); });
    return () => { live = false; if (!settled) controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [material.id, material.folderPath, entry, store, size, failed]);
  return url ? <img src={url} alt={`${material.materialName} — ${entry.name}`} width={size} height={size} decoding="async" onError={failed} />
    : <span className="gallery-placeholder" role="status">Loading preview…</span>;
}

function TilePreview({ material, store, size, selectedName, select, compact = false }: { material: Material; store: GalleryStore; size: 256 | 512; selectedName: string; select: (name: string) => void; compact?: boolean }) {
  const [listing, setListing] = useState<{ items: PreviewEntry[]; missing: boolean }>();
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); let live = true; let settled = false;
    void Promise.resolve().then(() => live ? store.listing(material.id, material.folderPath!, controller.signal) : undefined).then(value => {
      settled = true; if (live && value) setListing({ ...value, items: orderedGalleryEntries(value.items) });
    }, () => { settled = true; if (live) setError(true); });
    return () => { live = false; if (!settled) controller.abort(); };
  }, [material.id, material.folderPath, store, attempt]);
  // Stable callback keeps an already decoded image alive across parent renders.
  const fail = useCallback(() => setError(true), []);
  if (error) return <span className="gallery-placeholder">Preview unavailable<button className="gallery-retry" onClick={() => { store.forget(material.id, material.folderPath!); setError(false); setListing(undefined); setAttempt(value => value + 1); }}>Retry preview</button></span>;
  if (!listing) return <span className="gallery-placeholder" role="status">Loading preview…</span>;
  if (!listing.items.length) return <span className="gallery-placeholder">{listing.missing ? "No PREVIEW folder" : "No PNG previews"}</span>;
  const index = Math.max(0, listing.items.findIndex(item => item.name === selectedName));
  const selected = listing.items[index];
  return <>
    <TileImage key={`${selected.name}:${selected.sha256}:${size}:${attempt}`} material={material} entry={selected} store={store} size={size} failed={fail} />
    {!compact && listing.items.length > 1 && <div className="gallery-arrows" aria-label={`Previews for ${material.materialName}`}>
      <button aria-label={`Previous preview of ${material.materialName}`} title="Previous preview" onClick={() => select(listing.items[(index - 1 + listing.items.length) % listing.items.length].name)}>‹</button>
      <span aria-live="polite">{index % listing.items.length + 1}/{listing.items.length}</span>
      <button aria-label={`Next preview of ${material.materialName}`} title="Next preview" onClick={() => select(listing.items[(index + 1) % listing.items.length].name)}>›</button>
    </div>}
  </>;
}

function MaterialTile({ material, store, size, navigate }: { material: Material; store: GalleryStore; size: GallerySize; navigate: (path: string) => void }) {
  const element = useRef<HTMLLIElement>(null);
  const [near, setNear] = useState(false);
  const [selectedName, select] = useState("");
  useEffect(() => {
    if (!element.current) return;
    if (typeof IntersectionObserver === "undefined") { const timer = setTimeout(() => setNear(true), 0); return () => clearTimeout(timer); }
    const observer = new IntersectionObserver(entries => setNear(entries[0].isIntersecting), { rootMargin: "250px" });
    observer.observe(element.current); return () => observer.disconnect();
  }, []);
  return <li ref={element} className="gallery-tile" aria-label={material.materialName}>
    <div className="gallery-image">
      {!material.folderPath ? <span className="gallery-placeholder">No folder linked</span>
        : near ? <TilePreview key={`${material.id}:${material.folderPath}`} material={material} store={store} size={size === "small" ? 256 : 512} selectedName={selectedName} select={select} />
          : <span className="gallery-placeholder" aria-hidden="true" />}
      <NavigationLink className="gallery-open" href={`/materials/${material.id}`} navigate={navigate} aria-label={`Open ${material.materialName}`} />
    </div>
    <div className="gallery-caption"><NavigationLink href={`/materials/${material.id}`} navigate={navigate} title={material.materialName}>{material.materialName}</NavigationLink>
      <span title={material.technicalIdentity}>{material.technicalIdentity}</span></div>
  </li>;
}

export function MaterialsGrid({ materials, store, size, navigate }: { materials: Material[]; store: GalleryStore; size: GallerySize; navigate: (path: string) => void }) {
  return <ul className={`materials-grid materials-grid--${size}`} aria-label="Material gallery">{materials.map(material => <MaterialTile key={material.id} material={material} store={store} size={size} navigate={navigate} />)}</ul>;
}

export function MaterialThumbnail({ material, store }: { material: Material; store: GalleryStore }) {
  const element = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false);
  const select = useCallback(() => {}, []);
  useEffect(() => {
    if (!element.current) return;
    if (typeof IntersectionObserver === "undefined") { const timer = setTimeout(() => setNear(true), 0); return () => clearTimeout(timer); }
    const observer = new IntersectionObserver(entries => setNear(entries[0].isIntersecting), { rootMargin: "150px" });
    observer.observe(element.current); return () => observer.disconnect();
  }, []);
  return <div className="material-thumbnail" ref={element} aria-label={`Preview of ${material.materialName}`}>
    {!material.folderPath ? <span title="No folder linked">—</span> : near
      ? <TilePreview key={`${material.id}:${material.folderPath}`} material={material} store={store} size={256} selectedName="" select={select} compact /> : null}
  </div>;
}
