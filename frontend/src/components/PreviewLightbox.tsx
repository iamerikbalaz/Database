import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { previewClient, type PreviewEntry } from "../api/previewClient";
import { Icon } from "./Icon";
import "./PreviewLightbox.css";

function OriginalImage({ materialId, entry, actualSize }: { materialId: string; entry: PreviewEntry; actualSize: boolean }) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ attempt: number; url?: string; width?: number; height?: number; failed?: boolean }>();
  useEffect(() => {
    const controller = new AbortController(); let live = true, settled = false, url = "";
    void Promise.resolve().then(() => live ? previewClient.original(materialId, entry, controller.signal) : undefined).then(image => {
      settled = true;
      if (live && image) { url = URL.createObjectURL(image.blob); setResult({ attempt, url, width: image.width, height: image.height }); }
    }, () => { settled = true; if (live) setResult({ attempt, failed: true }); });
    return () => { live = false; if (!settled) controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [materialId, entry, attempt]);
  const current = result?.attempt === attempt ? result : undefined;
  return <>
    <div className={`preview-lightbox-viewport${actualSize ? " preview-lightbox-viewport--actual" : ""}`}>
      {current?.failed ? <div role="alert"><p>The original preview could not be loaded. Its source may have changed; close this view and reload the gallery.</p><button type="button" className="button" onClick={() => setAttempt(value => value + 1)}>Retry original preview</button></div> : current?.url ?
        <img src={current.url} alt={`Full-quality preview: ${entry.name}`} width={current.width} height={current.height}
          style={actualSize ? { width: current.width, height: current.height } : undefined}
          onError={() => setResult({ attempt, failed: true })} /> : <p role="status">Loading full-quality preview…</p>}
    </div>
    <p className="preview-lightbox-caption" aria-live="polite">{entry.name}{current?.width && current.height ? ` · ${current.width} × ${current.height} px` : ""} · Full quality</p>
  </>;
}

export function PreviewLightbox({ materialId, entries: input, selectedName, onClose }: {
  materialId: string; entries: PreviewEntry[]; selectedName: string; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [entries] = useState(() => [...input]);
  const [index, setIndex] = useState(() => Math.max(0, input.findIndex(entry => entry.name === selectedName)));
  const [actualSize, setActualSize] = useState(false);
  const selected = entries[index];
  const move = (step: number) => { if (entries.length > 1) setIndex(value => (value + entries.length + step) % entries.length); };
  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.showModal();
    return () => { if (trigger?.isConnected) trigger.focus(); };
  }, []);
  return createPortal(<dialog ref={dialog} className="preview-lightbox" aria-label="Full-quality material previews"
    onClick={event => event.stopPropagation()} onCancel={event => { event.preventDefault(); onClose(); }}
    onKeyDown={event => {
      event.stopPropagation();
      if (!event.ctrlKey && !event.altKey && !event.metaKey && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
        event.preventDefault(); move(event.key === "ArrowLeft" ? -1 : 1);
      }
    }}>
    <div className="preview-lightbox-toolbar">
      <div className="preview-lightbox-navigation" aria-label="Full-quality preview navigation">
        <button type="button" className="button preview-lightbox-icon" title="Previous original preview" aria-label="Previous original preview" disabled={entries.length < 2} onClick={() => move(-1)}><Icon name="back" size={18} /></button>
        <span>{entries.length ? index + 1 : 0} / {entries.length}</span>
        <button type="button" className="button preview-lightbox-icon" title="Next original preview" aria-label="Next original preview" disabled={entries.length < 2} onClick={() => move(1)}><Icon name="arrow" size={18} /></button>
      </div>
      <button type="button" className="button" aria-pressed={actualSize} title="Show one image pixel per CSS pixel" onClick={() => setActualSize(value => !value)}>{actualSize ? "Fit to window" : "1:1 pixels"}</button>
      <button type="button" className="button" onClick={onClose}>Close preview</button>
    </div>
    {selected ? <OriginalImage key={`${selected.name}:${selected.sha256}`} materialId={materialId} entry={selected} actualSize={actualSize} /> : <p>No previews available.</p>}
  </dialog>, document.body);
}
