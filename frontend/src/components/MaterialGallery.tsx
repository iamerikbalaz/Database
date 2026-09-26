import { useCallback, useEffect, useId, useState } from "react";
import { previewClient, type PreviewEntry } from "../api/previewClient";
import { useResource } from "../api/useResource";
import { ErrorState, LoadingState } from "./PageState";

function PreviewImage({ materialId, entry }: { materialId: string; entry: PreviewEntry }) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ attempt: number; url?: string; width?: number; height?: number; failed?: boolean }>();
  useEffect(() => {
    const controller = new AbortController(); let active = true; let settled = false; let objectUrl: string | undefined;
    void Promise.resolve().then(() => active ? previewClient.image(materialId, entry, controller.signal) : undefined).then((image) => {
      settled = true;
      if (!active || !image) return;
      objectUrl = URL.createObjectURL(image.blob);
      setResult({ attempt, url: objectUrl, width: image.width, height: image.height });
    }, () => { settled = true; if (active) setResult({ attempt, failed: true }); });
    return () => { active = false; if (!settled) controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [materialId, entry, attempt]);
  const current = result?.attempt === attempt ? result : undefined;
  if (current?.failed) return <ErrorState message="This preview could not be loaded. Reload the gallery if its source changed." retry={() => setAttempt((value) => value + 1)} />;
  if (!current?.url) return <LoadingState label="Loading preview…" />;
  return <figure className="material-preview"><img src={current.url} alt={`Preview: ${entry.name}`} width={current.width} height={current.height}
    onError={() => setResult({ attempt, failed: true })} /><figcaption>{entry.name} · {current.width} × {current.height} px</figcaption></figure>;
}

function GalleryContents({ materialId }: { materialId: string }) {
  const load = useCallback(() => previewClient.listing(materialId), [materialId]);
  const resource = useResource(load), selectId = useId();
  const [selectedName, setSelectedName] = useState("");
  const selected = resource.data?.items.find((item) => item.name === selectedName)
    ?? resource.data?.items.find((item) => item.name.toUpperCase() === "FABRIC_1.PNG")
    ?? resource.data?.items.find((item) => item.name.toUpperCase() === "SPHERE_1.PNG")
    ?? resource.data?.items[0];
  return <>
    {resource.error ? <ErrorState message="The preview gallery could not be loaded. Check access, the source folder and any active identity operation." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading gallery…" /> : <>
      {resource.data.missing ? <p>The linked material has no PREVIEW folder.</p> : resource.data.items.length === 0 ? <p>No supported preview images were found.</p> : <>
        <label htmlFor={selectId}>Preview image</label><select id={selectId} value={selected?.name ?? ""} onChange={(event) => setSelectedName(event.target.value)}>
          {resource.data.items.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
        </select>
        {selected && <PreviewImage key={`${materialId}-${selected.name}-${selected.sha256}`} materialId={materialId} entry={selected} />}
      </>}
      {resource.data.ignoredEntries > 0 && <p>{resource.data.ignoredEntries} other entries were omitted. The gallery shows images directly inside PREVIEW.</p>}
    </>}
    <button className="button" onClick={resource.retry}>Reload preview gallery</button>
  </>;
}

export function MaterialGallery({ materialId, linked, initiallyOpen = false }: { materialId: string; linked: boolean; initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  return <article className="panel preview-gallery" aria-label="Preview gallery"><h2>Preview gallery</h2>
    <p>JPEG, PNG, TIFF and WebP previews are shown at up to 1024 px. These display images are for visual review; color and bit depth can differ from the source.</p>
    {!linked ? <p>Link a material folder to view its previews.</p> : !open ? <button className="button" onClick={() => setOpen(true)}>Open preview gallery</button> : <>
      <GalleryContents materialId={materialId} /><button className="button" onClick={() => setOpen(false)}>Close preview gallery</button>
    </>}
  </article>;
}
