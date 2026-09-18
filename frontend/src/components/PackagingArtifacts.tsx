import { useEffect, useRef, useState } from "react";
import { packagingClient, type PackagingJob } from "../api/packagingClient";

export function PackagingArtifacts({ materialId, job }: { materialId: string; job: Pick<PackagingJob, "id" | "proofSha256"> }) {
  const [page, setPage] = useState<Awaited<ReturnType<typeof packagingClient.files>> | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const mounted = useRef(true), loading = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const load = async (more: boolean) => {
    if (loading.current) return;
    loading.current = true; setBusy(true); setError("");
    try {
      const next = await packagingClient.files(materialId, job, more ? page?.nextCursor ?? null : null);
      const items = more ? [...(page?.items ?? []), ...next.items] : next.items;
      if (new Set(items.map((item) => item.id)).size !== items.length) throw new Error("Duplicate packaged files");
      if (mounted.current) setPage({ ...next, items });
    } catch {
      if (mounted.current) setError("Packaged files could not be loaded. Refresh your access and try again.");
    } finally { loading.current = false; if (mounted.current) setBusy(false); }
  };
  return <section aria-label="Packaged files">
    <h4>Packaged files</h4>
    <p>These files belong to this completed job. Your browser reports download progress and completion.</p>
    {error && <p role="alert">{error}</p>}
    <button className="button" disabled={busy} onClick={() => void load(false)}>Load packaged files</button>
    {page && <ul>{page.items.map((item) => <li key={item.id}>
      <a href={packagingClient.artifactUrl(materialId, job, item.id)} download target="_blank" rel="noopener noreferrer">Download {item.path}</a>
      <span> · {item.size.toLocaleString()} bytes</span>
    </li>)}</ul>}
    {page?.nextCursor !== null && page?.nextCursor !== undefined && <button className="button" disabled={busy} onClick={() => void load(true)}>More packaged files</button>}
  </section>;
}
