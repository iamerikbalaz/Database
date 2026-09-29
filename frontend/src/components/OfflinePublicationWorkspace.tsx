import { useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import type { Material } from "../api/materialDto";
import { materialLocalClient } from "../api/materialLocalClient";
import { localPublicationClient, type LocalExportRequest, type LocalPublicationJob, type LocalPublicationPreview } from "../api/localPublicationClient";
import { sessionGeneration } from "../auth/sessionTransport";
import { useNavigationGuard } from "../navigationGuard";
import { CheckReportView, type CheckReport } from "./MaterialBulkCheck";
import { FileCheckProgress } from "./FileCheckProgress";
import { useFileCheckProgress } from "./useFileCheckProgress";
import { FileCheckJobUnavailableError } from "../api/materialCheckJobs";
import { NavigationLink } from "./NavigationLink";
import "./offlinePublication.css";

function finding(code: string, fields: string[] = []) {
  const labels: Record<string, string> = {
    CONTENT_DESCRIPTION_EMPTY: "Description is empty.", CONTENT_TAGS_EMPTY: "Tags are empty.",
    CONTENT_DRAFT_REQUIRED: "Save Material data for library on the material card.",
    CONTENT_CREDITS_REQUIRED: "Set the material's Credits value.", EXPORT_CREDITS_INVALID: "Set the material's Credits value.",
    CONTENT_CATEGORIES_REQUIRED: "Choose at least one online category.",
    CONTENT_BRAND_INACTIVE: "The published brand is inactive.",
    CONTENT_CATALOG_VALUE_INACTIVE: "A selected category or brand collection is inactive.",
    CONTENT_COLLECTION_BRAND_MISMATCH: "A selected collection belongs to another brand.",
    CSV_FORMULA_LIKE_VALUE: `Formula-like text in ${fields.join(", ")}. Use the intended library importer or a text viewer to inspect the CSV.`,
    MATERIAL_DONE_REQUIRED: "Set Status to Done before preparing publication.",
    FOLDER_REQUIRED: "Link the material data folder.", MATERIAL_OPERATION_ACTIVE: "Another operation is using this material.",
    EXPORT_WIDTH_CM_INVALID: "Sample width is missing or invalid.", EXPORT_HEIGHT_CM_INVALID: "Sample height is missing or invalid.",
    EXPORT_COLOR_INVALID: "Choose a valid color.", EXPORT_BRAND_IDENTIFIER_INVALID: "Set the brand identifier.",
    METADATA_SOURCE_DATABASE_MISMATCH: "The metadata file differs from the database. Reload or save the material data before export.",
    METADATA_EXPORT_INVALID: "The metadata file is missing or invalid.",
    SOURCE_METADATA_UNAVAILABLE: "The source metadata file could not be read.",
  };
  return labels[code] ?? code.replaceAll("_", " ").toLowerCase();
}

export function OfflinePublicationWorkspace({ client, navigate, initialSelection, onBusyChange, onChanged }: {
  client: ApiClient; navigate: (path: string) => void; initialSelection: Material[];
  onBusyChange?: (busy: boolean) => void; onChanged?: () => void;
}) {
  const [materialIds] = useState(() => initialSelection.map(item => item.id));
  const [automatic, setAutomatic] = useState(false), [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<LocalPublicationPreview | null>(null), [checkReport, setCheckReport] = useState<CheckReport | null>(null);
  const [job, setJob] = useState<LocalPublicationJob | null>(null), [uncertain, setUncertain] = useState(false), [pollPaused, setPollPaused] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [askPublished, setAskPublished] = useState(false), [markBusy, setMarkBusy] = useState(false), [markUncertain, setMarkUncertain] = useState(false);
  const [markError, setMarkError] = useState("");
  const operating = useRef(false), mounted = useRef(true), pending = useRef<LocalExportRequest | null>(null), markKey = useRef<string | null>(null);
  const dialog = useRef<HTMLDialogElement>(null), generation = useRef(sessionGeneration());
  const checkProgress = useFileCheckProgress();
  const live = () => mounted.current && generation.current === sessionGeneration();
  const frozen = busy || uncertain || job?.status === "RUNNING" || markBusy || markUncertain;
  useNavigationGuard(() => operating.current || pending.current !== null || markKey.current !== null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { onBusyChange?.(frozen); return () => onBusyChange?.(false); }, [frozen, onBusyChange]);
  useEffect(() => { if (askPublished) dialog.current?.showModal(); else dialog.current?.close(); }, [askPublished]);

  const acceptJob = (result: LocalPublicationJob) => {
    if (!live()) return;
    setJob(result); setUncertain(false); setError("");
    if (result.status !== "RUNNING") pending.current = null;
    if (result.status === "COMPLETED") { setNotice("CSV and ZIP files are ready for manual upload."); setAskPublished(!result.published); }
    if (result.status === "FAILED") setError(`Publication files could not be completed: ${finding(result.errorCode ?? "EXPORT_FAILED")}. Review the materials before preparing a new export.`);
  };
  useEffect(() => {
    if (job?.status !== "RUNNING" || pollPaused) return;
    const id = job.id, controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const current = await localPublicationClient.detail(id, materialIds, controller.signal);
        if (controller.signal.aborted || !live()) return;
        acceptJob(current);
        if (current.status === "RUNNING") timer = setTimeout(() => void poll(), 1500);
      } catch {
        if (!controller.signal.aborted && live()) { setPollPaused(true); setError("The preparation result could not be read. Resume checking the same export; a second export is not needed."); }
      }
    };
    timer = setTimeout(() => void poll(), 1000);
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
    // The selection and session are frozen for this workspace.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, job?.status, pollPaused, materialIds]);

  const review = async () => {
    if (operating.current || pending.current || frozen) return;
    operating.current = true; setBusy(true); setError(""); setNotice(""); setPreview(null); setJob(null); setCheckReport(null);
    try {
      if (automatic) {
        const current = await Promise.all(materialIds.map(id => client.getMaterial(id)));
        if (!live()) return;
        const checked = await materialLocalClient.checkMany(current, true, checkProgress.begin());
        checkProgress.finish();
        if (!live()) return;
        setCheckReport(checked); onChanged?.();
      }
      const result = await localPublicationClient.preview(materialIds);
      if (live()) setPreview(result);
    } catch (cause) {
      if (live()) setError(cause instanceof FileCheckJobUnavailableError ? cause.message : cause instanceof ApiError && cause.status === 503
        ? "Local publication preparation is unavailable. Check the desktop connection and try again."
        : "Materials could not be reviewed. Refresh their data, check the source connection and try again.");
    } finally { operating.current = false; checkProgress.finish(); if (mounted.current) setBusy(false); }
  };
  const prepare = async () => {
    if (operating.current || !pending.current && (!preview?.canPrepare || frozen)) return;
    operating.current = true; setBusy(true); setError(""); setNotice("");
    try {
      if (!pending.current) {
        const destination = await localPublicationClient.destination();
        if (!live()) return;
        if (!destination) { setNotice("Folder selection canceled. Nothing was exported."); return; }
        pending.current = { material_ids: materialIds, expected_preview_hash: preview!.previewHash, destination_token: destination.token, idempotency_key: crypto.randomUUID() };
      }
      acceptJob(await localPublicationClient.create(pending.current));
    } catch (cause) {
      if (!live()) return;
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        pending.current = null; setUncertain(false); setPreview(null);
        setError("The materials or destination changed. Review the materials again before choosing the destination.");
      } else if (pending.current) { setUncertain(true); setError("The export outcome is unknown. Recover the same preparation request before leaving this page."); }
      else setError("The destination picker could not open. Check the local desktop connection and try again.");
    } finally { operating.current = false; if (mounted.current) setBusy(false); }
  };
  const markPublished = async () => {
    if (!job || job.status !== "COMPLETED" || operating.current) return;
    operating.current = true; markKey.current ??= crypto.randomUUID(); setMarkBusy(true); setMarkError("");
    try {
      await localPublicationClient.markPublished(job.id, materialIds, markKey.current);
      markKey.current = null;
      if (live()) { setJob({ ...job, published: true }); setAskPublished(false); setMarkUncertain(false); onChanged?.(); }
    } catch (cause) {
      if (!live()) return;
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        markKey.current = null; setMarkUncertain(false);
        setMarkError("Published could not be changed. Material data or permissions may have changed since export. The prepared files remain available.");
      } else { setMarkUncertain(true); setMarkError("The result is unknown. Retry the same confirmation to recover it safely."); }
    } finally { operating.current = false; if (mounted.current) setMarkBusy(false); }
  };
  return <div className="offline-publication">
    <fieldset className="panel" disabled={frozen}><legend>Selected materials ({materialIds.length}/100)</legend>
      <details><summary>Show selected materials</summary><ul>{initialSelection.map(item => <li key={item.id}>{item.materialName} <small>{item.technicalIdentity}</small></li>)}</ul></details>
      <label className="checkbox-label"><input type="checkbox" checked={automatic} onChange={event => { setAutomatic(event.target.checked); setPreview(null); setCheckReport(null); }} />Run automatic file check</label>
      {automatic && <p className="muted">Review includes the full map, metadata and preview checks. The TXT report lists materials with issues. Large selections may take several minutes.</p>}
      <button type="button" className="button" disabled={!materialIds.length || materialIds.length > 100} onClick={() => void review()}>{busy ? "Working…" : "Review materials"}</button>
    </fieldset>
    {checkProgress.running && <FileCheckProgress progress={checkProgress.progress} resume={checkProgress.resume} total={materialIds.length} />}
    {error && <p role="alert" className="form-error">{error}</p>}{notice && <p role="status" className="success-notice">{notice}</p>}
    {job?.status === "FAILED" && job.issues.length > 0 && <section className="panel" aria-label="Export file issues"><h3>File issues</h3>
      {job.issues.map((group, index) => <article key={`${group.materialId}-${index}`}><h4>{initialSelection.find(item => item.id === group.materialId)?.materialName ?? group.materialId}</h4>
        <ul>{group.issues.map((issue, index) => <li key={index}>{finding(issue.code)}{issue.path && <> · <code>{issue.path}</code></>}</li>)}</ul>
      </article>)}
    </section>}
    {checkReport && <CheckReportView result={checkReport} />}
    {preview && <section className="panel" aria-label="Publication review"><h3>Material review</h3>
      <p>{preview.canPrepare ? "The required publication data is complete. Review any warnings before preparing the files." : "Complete the required publication data before preparing the files."}</p>
      {preview.items.map(item => <article className="offline-publication-item" key={item.materialId}><h4>{item.name}</h4><p>{item.identity}</p>
        {!!item.errors.length && <ul className="form-error">{[...new Set(item.errors.map(code => finding(code)))].map(message => <li key={message}>{message}</li>)}</ul>}
        {!!item.warnings.length && <ul className="publication-warnings">{item.warnings.map((warning, index) => <li key={index}>{finding(warning.code, warning.fields)}</li>)}</ul>}
        {item.row && <details><summary>Export values</summary><dl className="publication-row">
          <dt>Description</dt><dd>{item.row.description || "Empty"}</dd><dt>Credits</dt><dd>{item.row.credits}</dd>
          <dt>Sample size</dt><dd>{item.row.widthCm} × {item.row.heightCm} cm</dd><dt>Brand identifier</dt><dd>{item.row.brandIdentifier}</dd>
          <dt>Categories</dt><dd>{item.row.categories.join(", ")}</dd><dt>Color</dt><dd>{item.row.color}</dd><dt>Tags</dt><dd>{item.row.tags.join(", ") || "Empty"}</dd>
        </dl></details>}
        <NavigationLink href={`/materials/${item.materialId}`} navigate={navigate}>Open material</NavigationLink>
      </article>)}
    </section>}
    {!job || job.status === "FAILED" ? <button type="button" className="button button--primary" disabled={frozen && !uncertain || !uncertain && !preview?.canPrepare} onClick={() => void prepare()}>
      {uncertain ? "Recover preparation" : busy ? "Working…" : "Prepare publication"}
    </button> : null}
    {job?.status === "RUNNING" && <div role="status" className="panel"><p>Preparing CSV and ZIP files… Large materials may take several minutes.</p>
      {pollPaused && <button type="button" className="button" onClick={() => { setError(""); setPollPaused(false); }}>Resume checking export</button>}
    </div>}
    {job?.status === "COMPLETED" && <section className="panel" aria-label="Prepared publication files"><h3>Publication files ready</h3>
      <p className="folder-absolute-path">{job.outputPath}</p><p>{job.csvName} · {job.archives.length} ZIP archives</p>
      <details><summary>Show ZIP files</summary><ul>{job.archives.map(item => <li key={item.name}>{item.name} <small>({(item.sizeBytes / 1024 ** 2).toFixed(1)} MB)</small></li>)}</ul></details>
      <p>Upload these files and the CSV manually when ready.</p><p>Published: {job.published ? "Yes" : "Unchanged"}</p>
      {!job.published && <button className="button" type="button" onClick={() => setAskPublished(true)}>Mark exported materials as Published…</button>}
    </section>}
    <dialog ref={dialog} className="confirm-dialog" aria-labelledby="mark-exported-published" onCancel={event => { event.preventDefault(); if (!markBusy && !markUncertain) setAskPublished(false); }}>
      <div className="confirm-dialog__body"><h2 id="mark-exported-published">Mark materials as Published?</h2>
        <p>CSV and ZIP files were prepared for {materialIds.length} materials. Mark these materials as Published now?</p>
        <p>This records your confirmation. File upload remains manual.</p>{markError && <p role="alert" className="form-error">{markError}</p>}
        <div className="form-actions"><button type="button" className="button" disabled={markBusy || markUncertain} onClick={() => setAskPublished(false)}>Not now</button>
          <button type="button" className="button button--primary" disabled={markBusy} onClick={() => void markPublished()}>{markBusy ? "Saving…" : markUncertain ? "Retry same confirmation" : "Yes, mark Published"}</button></div>
      </div>
    </dialog>
  </div>;
}
