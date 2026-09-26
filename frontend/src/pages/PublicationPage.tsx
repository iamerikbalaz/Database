import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import type { Material } from "../api/materialDto";
import { ApiError } from "../api/errors";
import { publicationClient, type PublicationBatch, type PublicationCreate, type PublicationPreview, type PublicationRow, type PublicationWarning } from "../api/publicationClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { NavigationLink } from "../components/NavigationLink";
import { PackagingJobsPanel } from "../components/PackagingJobsPanel";
import { StagingPanel } from "../components/StagingPanel";
import { useNavigationGuard } from "../navigationGuard";

function Warnings({ items }: { items: PublicationWarning[] }) {
  return items.length ? <ul className="publication-warnings">{items.map((warning, index) => <li key={index}>
    {warning.code === "CONTENT_DESCRIPTION_EMPTY" ? "Description is empty." : warning.code === "CONTENT_TAGS_EMPTY" ? "Tags are empty." : `Formula-like text in ${warning.fields.join(", ")}. Use this CSV with the intended importer or a plain text viewer; spreadsheet software may execute it as a formula.`}
  </li>)}</ul> : null;
}
function Row({ value }: { value: PublicationRow }) {
  return <dl className="publication-row">
    <dt>Identity</dt><dd>{value.identityName}</dd><dt>Name</dt><dd>{value.name}</dd>
    <dt>Description</dt><dd className="publication-text">{value.description || "Empty"}</dd>
    <dt>Credits</dt><dd>{value.credits}</dd><dt>Dimension</dt><dd>{value.widthCm}x{value.heightCm} cm</dd>
    <dt>Brand identifier</dt><dd>{value.brandIdentifier}</dd><dt>Categories</dt><dd>{value.categories.join(":")}</dd>
    <dt>Color</dt><dd>{value.color}</dd><dt>Tags</dt><dd>{value.tags.join(":") || "Empty"}</dd>
  </dl>;
}
export function PublicationPage({ client, navigate }: { client: ApiClient; navigate: (path: string) => void }) {
  const role = useSession()?.session.user.role;
  return <section className="publication-page"><div className="page-heading"><div><p className="eyebrow">Publishing preparation</p><h1>Publication</h1>
    <p>Select reviewed materials, inspect their export values and save an immutable CSV batch.</p></div></div>
    {role === "ADMIN" || role === "LEADERSHIP" ? <PublicationWorkspace client={client} navigate={navigate} /> : <p>Your role does not allow publication preparation.</p>}
  </section>;
}
export function PublicationWorkspace({ client, navigate, initialSelection, onBusyChange }: {
  client: ApiClient; navigate: (path: string) => void; initialSelection?: Material[];
  onBusyChange?: (busy: boolean) => void;
}) {
  const [materials, setMaterials] = useState<Material[] | null>(null), [offset, setOffset] = useState(0);
  const [search, setSearch] = useState(""), [projectId, setProjectId] = useState("");
  const [selected, setSelected] = useState<Material[]>(() => initialSelection?.map(item => ({ ...item })) ?? []), [preview, setPreview] = useState<PublicationPreview | null>(null);
  const [reason, setReason] = useState(""), [reviewed, setReviewed] = useState(false), [acknowledged, setAcknowledged] = useState(false);
  const [batch, setBatch] = useState<PublicationBatch | null>(null), [history, setHistory] = useState<Awaited<ReturnType<typeof publicationClient.history>> | null>(null);
  const [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const pending = useRef<PublicationCreate | null>(null), operating = useRef(false), mounted = useRef(true), download = useRef<AbortController | null>(null);
  const frozen = busy || uncertain;
  useNavigationGuard(() => pending.current !== null);
  useEffect(() => { onBusyChange?.(frozen); return () => onBusyChange?.(false); }, [frozen, onBusyChange]);
  useEffect(() => { mounted.current = true; const prevent = (event: BeforeUnloadEvent) => { if (pending.current) { event.preventDefault(); event.returnValue = ""; } }; window.addEventListener("beforeunload", prevent);
    return () => { mounted.current = false; download.current?.abort(); window.removeEventListener("beforeunload", prevent); }; }, []);
  const loadProjects = useCallback(() => initialSelection ? Promise.resolve([]) : client.getProjects(), [client, initialSelection]);
  const projects = useResource(loadProjects);
  const resetPreview = () => { setPreview(null); setReviewed(false); setAcknowledged(false); setNotice(""); };
  const run = async (action: () => Promise<void>, message: string) => {
    if (operating.current || pending.current) return;
    operating.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); } catch { if (mounted.current) setError(message); }
    finally { operating.current = false; if (mounted.current) setBusy(false); }
  };
  const find = () => run(async () => { const found = await client.getMaterials({ workflow_status: "DONE", project_id: projectId, search }); if (mounted.current) { setMaterials(found); setOffset(0); } }, "Materials could not be loaded. Retry the search.");
  const inspect = () => run(async () => { resetPreview(); const result = await publicationClient.preview(selected.map((item) => item.id)); if (mounted.current) setPreview(result); }, "The preview could not be loaded. Check access and retry.");
  const send = async () => {
    if (operating.current || !pending.current) return;
    operating.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const saved = await publicationClient.create(pending.current);
      pending.current = null;
      if (mounted.current) { setBatch(saved); setPreview(null); setReviewed(false); setAcknowledged(false); setUncertain(false); setHistory(null); setNotice("CSV batch saved. Packaging, upload and online publication are still pending."); }
    } catch (cause) {
      if (!uncertain && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        pending.current = null;
        if (mounted.current) { setUncertain(false); resetPreview(); setError(cause.status === 409 ? "Inputs or approvals changed, or this request conflicts with an earlier batch. Review history and load a new preview." : "The batch was rejected. Check your access, selection and acknowledgments, then load a new preview."); }
      } else if (mounted.current) { setUncertain(true); setError("The outcome is unknown. Stay on this page and retry the same request to recover its saved result without creating a duplicate batch."); }
    } finally { operating.current = false; if (mounted.current) setBusy(false); }
  };
  const save = () => {
    if (frozen || pending.current || !preview?.canPrepare || !reviewed || !reason.trim() || (preview.items.some((item) => item.warnings.length) && !acknowledged)) return;
    pending.current = { material_ids: selected.map((item) => item.id), idempotency_key: crypto.randomUUID(), expected_preview_hash: preview.previewHash, reason: reason.trim(), warnings_acknowledged: acknowledged };
    void send();
  };
  const loadHistory = (after: string | null = null) => run(async () => { const page = await publicationClient.history(after); if (mounted.current) setHistory(page); }, "Batch history could not be loaded.");
  const openBatch = (id: string) => run(async () => { setBatch(null); const result = await publicationClient.detail(id); if (mounted.current) setBatch(result); }, "The saved batch could not be loaded.");
  const downloadCsv = () => run(async () => {
    if (!batch) return;
    const controller = new AbortController(); download.current = controller;
    const timer = window.setTimeout(() => controller.abort(), 60000);
    try {
      const blob = await publicationClient.csv(batch, controller.signal);
      if (!mounted.current || controller.signal.aborted) return;
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = `publication-${batch.id}.csv`; document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice("The saved CSV passed its integrity check and is ready to download.");
    } finally { window.clearTimeout(timer); download.current = null; }
  }, "CSV download or its integrity check failed. Retry the saved batch download.");
  const hasWarnings = preview?.items.some((item) => item.warnings.length);
  return <>
    {error && <p role="alert" className="form-error">{error}</p>}{notice && <p role="status" className="success-notice">{notice}</p>}
    {uncertain && <button className="button" disabled={busy} onClick={() => void send()}>Retry same batch request</button>}
    <fieldset className="panel" disabled={frozen}><legend>1. Select materials</legend>
      {initialSelection ? <p>This selection is fixed from Materials. Review all findings before preparing CSV and ZIP files. Return to the list to choose a different set; each batch supports up to 100 materials.</p> : <p>Only DONE materials are listed. Each selected material must also pass the current technical and content approvals. Select up to 100.</p>}
      {!initialSelection && <>
      <div className="publication-filters"><label>Search materials<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        <label>Project<select value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">All projects</option>{projects.data?.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
        <button className="button" onClick={() => void find()}>Find materials</button></div>
      {projects.error && <p role="alert">Project options could not be loaded. <button className="button" onClick={projects.retry}>Retry project options</button></p>}
      {materials && <><p>{materials.length} matching materials.</p><ul className="publication-selection">{materials.slice(offset, offset + 50).map((material) => <li key={material.id}><label>
        <input type="checkbox" checked={selected.some((item) => item.id === material.id)} disabled={selected.length >= 100 && !selected.some((item) => item.id === material.id)} onChange={(event) => { setSelected(event.target.checked ? [...selected, material] : selected.filter((item) => item.id !== material.id)); resetPreview(); }} />
        <span>{material.materialName}<small>{material.technicalIdentity}</small></span></label></li>)}</ul>
        <div className="publication-actions"><button className="button" disabled={offset === 0} onClick={() => setOffset(offset - 50)}>Previous materials</button><button className="button" disabled={offset + 50 >= materials.length} onClick={() => setOffset(offset + 50)}>Next materials</button></div></>}
      </>}
      <h2>Selected materials ({selected.length}/100)</h2><ul>{selected.map((item) => <li key={item.id}>{item.materialName} — {item.technicalIdentity} {!initialSelection && <button className="button" aria-label={`Remove ${item.technicalIdentity}`} onClick={() => { setSelected(selected.filter((entry) => entry.id !== item.id)); resetPreview(); }}>Remove</button>}</li>)}</ul>
      <button className="button button--primary" disabled={!selected.length || selected.length > 100} onClick={() => void inspect()}>Review selected materials</button>
    </fieldset>
    {preview && <fieldset className="panel" disabled={frozen}><legend>2. Review export values</legend>
      <p>{preview.canPrepare ? "All selected materials passed the current approval checks." : "Preparation is blocked. Resolve the listed findings and load a new preview."}</p>
      {preview.items.map((item) => <article key={item.materialId}><h2>{item.name}</h2><NavigationLink href={`/materials/${item.materialId}`} navigate={navigate}>Open material</NavigationLink>
        {!!item.errors.length && <ul className="form-error">{item.errors.map((code) => <li key={code}>{code.replaceAll("_", " ").toLowerCase()}</li>)}</ul>}
        <Warnings items={item.warnings} />{item.row && <Row value={item.row} />}</article>)}
      <label>Reason for preparing this batch<textarea maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <label className="publication-check"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} />I reviewed the selected materials and export values.</label>
      {hasWarnings && <label className="publication-check"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />I acknowledge the export warnings above.</label>}
      <button className="button button--primary" disabled={!preview.canPrepare || !reviewed || !reason.trim() || (!!hasWarnings && !acknowledged)} onClick={save}>Save CSV batch</button>
    </fieldset>}
    <fieldset className="panel" disabled={frozen}><legend>Saved batch history</legend><p>Saved batches preserve the exact reviewed values. They can differ from current material records.</p>
      <button className="button" onClick={() => void loadHistory()}>Load latest batches</button>
      {history && <><ul className="publication-batches">{history.items.map((item) => <li key={item.id}><button className="button" onClick={() => void openBatch(item.id)}>Open batch {item.id}</button><span>{new Date(item.createdAt).toLocaleString()} · {item.rowCount} materials · CSV prepared</span></li>)}</ul>
        {!history.items.length && <p>No saved batches.</p>}{history.nextCursor && <button className="button" onClick={() => void loadHistory(history.nextCursor)}>Older batches</button>}</>}
    </fieldset>
    {batch && <fieldset className="panel" disabled={frozen}><legend>Saved CSV batch</legend><p className="revision-hash">Batch {batch.id}</p><p>{new Date(batch.createdAt).toLocaleString()} · {batch.rowCount} materials · CSV prepared</p>
      <p>Reason: {batch.reason}</p><p>This CSV is a historical snapshot. Review each material's packaging progress below. Upload and online publication have separate steps.</p>
      <p className="revision-hash">CSV SHA-256: {batch.csvSha256}</p><Warnings items={batch.warnings} />
      <button className="button button--primary" onClick={() => void downloadCsv()}>Download saved CSV</button>
      {batch.items.map((item) => <details key={item.materialId}><summary>{item.ordinal}. {item.row.name} — {item.row.identityName}</summary><Row value={item.row} />
        <p className="revision-hash">Asset revision: {item.row.revisionHash}</p><p className="revision-hash">Content snapshot: {item.row.contentContextHash}</p>
        <NavigationLink href={`/materials/${item.materialId}`} navigate={navigate}>Open material</NavigationLink>
        <PackagingJobsPanel key={`${batch.id}-${item.materialId}`} materialId={item.materialId} batch={{ id: batch.id, snapshotHash: item.snapshotHash }} />
      </details>)}
    </fieldset>}
    <StagingPanel batch={batch} />
  </>;
}
