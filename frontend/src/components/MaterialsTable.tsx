import { useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import { validateFolderPath } from "../api/folderPathValidation";
import type { GalleryStore } from "../api/galleryStore";
import { type InternalUser, type Material, checkedStatuses } from "../api/materialDto";
import { materialArchiveClient, lifecycleRequest, type ArchivePreview, type LifecycleRequest } from "../api/materialArchiveClient";
import { materialTableClient, type TableChange } from "../api/materialTableClient";
import { useSession } from "../auth/context";
import { sessionGeneration } from "../auth/sessionTransport";
import { categoryLabel, materialCategories } from "../data/materialCategories";
import type { Project, PublishedBrand } from "../types";
import { MaterialLifecyclePanel } from "./MaterialLifecyclePanel";
import { MaterialIdentityPanel } from "./MaterialIdentityPanel";
import { MaterialThumbnail } from "./MaterialsGrid";
import { NavigationLink } from "./NavigationLink";
import { useNavigationGuard } from "../navigationGuard";

const columns = [
  ["project", "Project", 180], ["brand", "Published brand", 180], ["category", "Category", 225],
  ["status", "Status", 140], ["checked", "Checked", 140], ["published", "Published", 100],
  ["archived", "Archived", 110], ["archivedAt", "Archive date", 200],
  ["processor", "Processor", 180], ["note", "Note", 240], ["folder", "Folder path", 340],
  ["number", "Number", 90], ["created", "Created", 200], ["updated", "Updated", 200],
  ["uuid", "Internal UUID", 310], ["technical", "File check", 145],
] as const;
type Column = typeof columns[number][0];
type Layout = { key: Column; visible: boolean; width: number }[];
const defaultLayout = (): Layout => columns.map(([key, , width]) => ({ key, width, visible: ["project", "brand", "category", "status", "checked", "published", "archived", "processor", "note"].includes(key) }));
function readLayout(): Layout {
  try {
    const value: unknown = JSON.parse(localStorage.getItem("materials.columns.v1") ?? "null");
    if (Array.isArray(value) && value.every(v => v && columns.some(([key]) => key === v.key) && typeof v.visible === "boolean")) return defaultLayout().map(column => ({ ...column, visible: value.find(v => v.key === column.key)?.visible ?? column.visible }));
  } catch { /* Invalid/disabled storage falls back to defaults. */ }
  return defaultLayout();
}
type EditField = "is_archived" | "workflow_status" | "checked_status" | "is_published" | "project_id" | "assigned_processor_id" | "note";
type Change = TableChange | { is_archived: boolean };
type Job = { material: Material; change: Change; lifecycle?: { preview: ArchivePreview; body: LifecycleRequest }; key: string; status: "waiting" | "saved" | "failed" | "unknown" | "stopped"; message?: string };
type Props = { materials: Material[]; store: GalleryStore; client: ApiClient; projects: Project[]; brands: PublishedBrand[]; users: InternalUser[];
  navigate: (path: string) => void; refresh: () => void; onBusyChange: (busy: boolean) => void;
  onPreparePublication?: (materials: Material[]) => void; detail?: boolean; onMaterialChanged?: (material: Material) => void };

function NoteCell({ material, disabled, save }: { material: Material; disabled: boolean; save: (change: TableChange) => void }) {
  const [draft, setDraft] = useState(material.note ?? "");
  return <div className="table-note"><textarea aria-label={`Note for ${material.materialName}`} rows={2} maxLength={10000} value={draft} disabled={disabled}
    placeholder="Add a note…" onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === "Escape") setDraft(material.note ?? ""); if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save({ note: draft || null }); } }} />
    {draft !== (material.note ?? "") && <button className="button" disabled={disabled} onClick={() => save({ note: draft || null })}>Save note</button>}</div>;
}

export function MaterialsTable({ materials, store, client, projects, brands, users, navigate, refresh, onBusyChange, onPreparePublication, detail = false, onMaterialChanged }: Props) {
  const actor = useSession()?.session.user;
  const role = actor?.role;
  const manager = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const editor = manager || role === "PROCESSOR";
  const publisher = role === "ADMIN" || role === "LEADERSHIP";
  const selectable = editor || (publisher && Boolean(onPreparePublication));
  const [layout, setLayout] = useState(readLayout);
  const [overrides, setOverrides] = useState<Record<string, Material>>({});
  const rows = materials.map(row => overrides[row.id] && overrides[row.id].updatedAt >= row.updatedAt ? overrides[row.id] : row);
  const archivedView = materials.every(row => row.isArchived);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkField, setBulkField] = useState<EditField>(materials.every(row => row.isArchived) ? "note" : "workflow_status");
  const [bulkValue, setBulkValue] = useState(materials.every(row => row.isArchived) ? "" : "DONE");
  const [jobs, setJobs] = useState<Job[]>([]);
  const jobsRef = useRef<Job[]>([]);
  const [pending, setPending] = useState(false);
  const [active, setActive] = useState(false);
  const [inline, setInline] = useState(false);
  const inlineRef = useRef(false);
  const [notice, setNotice] = useState("");
  const [identityBusy, setIdentityBusy] = useState(false);
  const [identity, setIdentity] = useState<{ material: Material; brand?: string; category?: string }>();
  const dialog = useRef<HTMLDialogElement>(null), identityDialog = useRef<HTMLDialogElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const alive = useRef(true), sending = useRef(false), stop = useRef(false);
  const generation = sessionGeneration();
  useNavigationGuard(() => sending.current || jobsRef.current.some(job => job.status === "unknown") || identityBusy);
  useEffect(() => { alive.current = true; return () => { alive.current = false; stop.current = true; }; }, []);
  useEffect(() => {
    onBusyChange(active || lifecycleBusy || Boolean(identity));
    return () => onBusyChange(false);
  }, [active, lifecycleBusy, identity, onBusyChange]);
  useEffect(() => {
    if (!active) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, [active]);
  useEffect(() => { if (identity) identityDialog.current?.showModal(); }, [identity]);
  const configure = (next: Layout) => { setLayout(next); try { localStorage.setItem("materials.columns.v1", JSON.stringify(next)); } catch { /* Memory preferences still work. */ } };
  const updateJob = (index: number, values: Partial<Job>) => { jobsRef.current = jobsRef.current.map((job, i) => i === index ? { ...job, ...values } : job); if (alive.current) setJobs(jobsRef.current); };
  const run = async () => {
    if (sending.current) return;
    sending.current = true; stop.current = false; setPending(true);
    for (let i = 0; i < jobsRef.current.length; i++) {
      const job = jobsRef.current[i];
      if (job.status !== "waiting" && job.status !== "unknown") continue;
      if (!alive.current || generation !== sessionGeneration()) break;
      if (stop.current) { updateJob(i, { status: "stopped", message: "Not attempted" }); continue; }
      let mutationStarted = Boolean(job.lifecycle);
      try {
        if ("is_archived" in job.change) {
          let packet = job.lifecycle;
          if (!packet) {
            const preview = await materialArchiveClient.preview(job.material.id, job.change.is_archived ? "ARCHIVE" : "RESTORE");
            if (!alive.current || generation !== sessionGeneration()) break;
            if (preview.isArchived === job.change.is_archived) {
              updateJob(i, { status: "saved", message: "Already saved" }); continue;
            }
            if (!preview.canApply) throw new ApiError(409, "Archived cannot change while another operation owns this material.");
            packet = { preview, body: lifecycleRequest(preview, undefined, job.key) };
            updateJob(i, { lifecycle: packet });
          }
          mutationStarted = true;
          await materialArchiveClient.command(packet.preview, actor!.id, packet.body);
          if (!alive.current || generation !== sessionGeneration()) break;
          updateJob(i, { status: "saved", message: "Saved" }); continue;
        }
        mutationStarted = true;
        const saved = await materialTableClient.update(job.material, job.change, job.key);
        if (!alive.current || generation !== sessionGeneration()) break;
        const updated = { ...saved, isArchived: job.material.isArchived, archivedAt: job.material.archivedAt };
        setOverrides(old => ({ ...old, [saved.id]: updated })); onMaterialChanged?.(updated);
        updateJob(i, { status: "saved", message: "Saved" });
      } catch (error) {
        if (!alive.current) break;
        if (!mutationStarted) {
          updateJob(i, { status: "failed", message: error instanceof ApiError && error.status === 409 ? error.message : "Current archive eligibility could not be verified. Nothing was changed." });
          if (!(error instanceof ApiError) || error.status !== 409) stop.current = true;
        } else if (job.status !== "unknown" && error instanceof ApiError && (error.status >= 400 && error.status < 500 || error.code === "TABLE_PREFLIGHT_UNAVAILABLE")) {
          updateJob(i, { status: "failed", message: error.code === "TABLE_PREFLIGHT_UNAVAILABLE" ? "Folder checking service unavailable. Nothing was changed; remaining materials were stopped." : error.status === 409 ? "Material changed or requirements not met. Reload before retrying." : error.message });
          if (error.status === 401 || error.status === 403 || error.code === "TABLE_PREFLIGHT_UNAVAILABLE") stop.current = true;
        } else {
          updateJob(i, { status: "unknown", message: "Outcome unknown. Retry the same request to recover it safely." });
          break;
        }
      }
    }
    sending.current = false;
    if (alive.current) {
      setPending(false);
      if (inlineRef.current && !jobsRef.current.some(j => j.status === "unknown")) setActive(false);
      setNotice(jobsRef.current.every(j => j.status === "saved") ? "Saved. Refresh materials to reapply the current filters." : "Review the results below.");
    }
  };
  const prepare = (targets: Material[], change: Change, immediate = false) => {
    if (sending.current || active || lifecycleBusy) return;
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    jobsRef.current = targets.map(material => ({ material: { ...material }, change: { ...change }, key: crypto.randomUUID(), status: "waiting" }));
    setJobs(jobsRef.current); setActive(true); setNotice(""); setInline(immediate); inlineRef.current = immediate;
    if (!immediate) dialog.current?.showModal();
    if (immediate) void run();
  };
  const selectedRows = rows.filter(row => selected.has(row.id));
  const bulkChoices = (field: EditField): { value: string; label: string }[] => {
    if (field === "workflow_status") return [{ value: "DONE", label: "Done" }, { value: "IN_PROGRESS", label: "In progress" }];
    if (field === "checked_status") return checkedStatuses.map(value => ({ value, label: value }));
    if (field === "is_published" || field === "is_archived") return [{ value: "true", label: "Yes" }, { value: "false", label: "No" }];
    if (field === "project_id") return [{ value: "", label: "No project assigned" }, ...projects.map(p => ({ value: p.id, label: p.name }))];
    if (field === "assigned_processor_id") return users.filter(u => u.isActive && u.role === "PROCESSOR").map(u => ({ value: u.id, label: u.displayName }));
    return [];
  };
  const bulkChange = (): Change => ({ [bulkField]: bulkField === "is_published" || bulkField === "is_archived" ? bulkValue === "true" : bulkValue || null }) as Change;
  const describeChange = (change: Change): [string, string] => {
    if ("workflow_status" in change) return ["Status", change.workflow_status === "DONE" ? "Done" : "In progress"];
    if ("checked_status" in change) return ["Checked", change.checked_status];
    if ("is_archived" in change) return ["Archived", change.is_archived ? "Yes" : "No"];
    if ("is_published" in change) return ["Published", change.is_published ? "Yes" : "No"];
    if ("project_id" in change) return ["Project", change.project_id === null ? "No project assigned" : projects.find(p => p.id === change.project_id)?.name ?? change.project_id];
    if ("assigned_processor_id" in change) return ["Processor", users.find(u => u.id === change.assigned_processor_id)?.displayName ?? change.assigned_processor_id];
    if ("published_brand_id" in change) return ["Published brand", brands.find(b => b.id === change.published_brand_id)?.name ?? change.published_brand_id];
    if ("main_category_code" in change) return ["Category", categoryLabel(change.main_category_code)];
    return ["Note", change.note ?? "Empty"];
  };
  const edit = (row: Material, change: TableChange) => prepare([row], change, true);
  const openIdentity = (material: Material, target: { brand?: string; category?: string }) => {
    if (!material.folderPath) {
      prepare([material], target.brand ? { published_brand_id: target.brand } : { main_category_code: target.category! });
      return;
    }
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setIdentity({ material, ...target });
  };
  const close = () => { if (pending || jobs.some(j => j.status === "unknown")) return; dialog.current?.close(); setActive(false); returnFocus.current?.focus(); if (jobs.some(job => "is_archived" in job.change && job.status === "saved")) refresh(); };
  const title = (key: Column) => columns.find(([value]) => value === key)![1];
  const renderCell = (key: Column, row: Material) => {
    const save = (change: TableChange) => edit(row, change);
    const disable = active || lifecycleBusy || Boolean(identity);
    const productionDisabled = disable || row.isArchived;
    if (key === "project") return manager ? <select aria-label={`Project for ${row.materialName}`} disabled={disable} value={row.projectId ?? ""} onChange={e => save({ project_id: e.target.value || null })}>
      <option value="">No project assigned</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select> : projects.find(p => p.id === row.projectId)?.name ?? row.projectId ?? "No project assigned";
    if (key === "brand") return manager ? <select aria-label={`Published brand for ${row.materialName}`} disabled={productionDisabled} value={row.publishedBrandId} onChange={e => openIdentity(row, { brand: e.target.value })}>
      {brands.filter(b => b.isActive || b.id === row.publishedBrandId).map(b => <option key={b.id} value={b.id}>{b.name}</option>)}</select> : brands.find(b => b.id === row.publishedBrandId)?.name ?? row.publishedBrandId;
    if (key === "category") return manager ? <select aria-label={`Category for ${row.materialName}`} disabled={productionDisabled} value={row.mainCategoryCode} onChange={e => openIdentity(row, { category: e.target.value })}>
      {!materialCategories.some(c => c.code === row.mainCategoryCode) && <option value={row.mainCategoryCode}>{categoryLabel(row.mainCategoryCode)}</option>}
      {materialCategories.map(c => <option key={c.code} value={c.code}>{categoryLabel(c.code)}</option>)}</select> : categoryLabel(row.mainCategoryCode);
    if (key === "status") return editor ? <select aria-label={`Status for ${row.materialName}`} disabled={productionDisabled} value={row.workflowStatus} onChange={e => save({ workflow_status: e.target.value as Material["workflowStatus"] })}>
      <option value="IN_PROGRESS">In progress</option><option value="DONE">Done</option></select> : row.workflowStatus === "DONE" ? "Done" : "In progress";
    if (key === "checked") return manager ? <select aria-label={`Checked for ${row.materialName}`} disabled={productionDisabled} value={row.checkedStatus} onChange={e => save({ checked_status: e.target.value as Material["checkedStatus"] })}>
      {checkedStatuses.map(value => <option key={value} value={value}>{value}</option>)}</select> : row.checkedStatus;
    if (key === "published") return <input type="checkbox" aria-label={`Published for ${row.materialName}`} disabled={!manager || disable} checked={row.isPublished} onChange={e => save({ is_published: e.target.checked })} />;
    if (key === "archived") return <MaterialLifecyclePanel materialId={row.id} archived={row.isArchived} label={`Archived for ${row.materialName}`} navigate={navigate} disabled={active || lifecycleBusy || Boolean(identity)} onBusyChange={setLifecycleBusy} onApplied={() => { refresh(); }} />;
    if (key === "archivedAt") return row.archivedAt ? new Date(row.archivedAt).toLocaleString() : "—";
    if (key === "processor") return manager ? <select aria-label={`Processor for ${row.materialName}`} disabled={disable} value={row.assignedProcessorId} onChange={e => save({ assigned_processor_id: e.target.value })}>
      {users.filter(u => u.id === row.assignedProcessorId || u.isActive && u.role === "PROCESSOR").map(u => <option key={u.id} value={u.id}>{u.displayName}{!u.isActive ? " (inactive)" : ""}</option>)}</select> : users.find(u => u.id === row.assignedProcessorId)?.displayName ?? row.assignedProcessorId;
    if (key === "note") return editor ? <NoteCell key={`${row.id}:${row.note ?? ""}`} material={row} disabled={disable} save={save} /> : <span className="note-text">{row.note ?? "—"}</span>;
    if (key === "folder") return <span className="material-folder-path">{row.folderPath ? validateFolderPath(row.folderPath).error ? "Unavailable (unsafe path hidden)" : row.folderPath : "No folder linked"}</span>;
    if (key === "number") return String(row.sequenceNumber).padStart(4, "0");
    if (key === "created") return new Date(row.createdAt).toLocaleString();
    if (key === "updated") return new Date(row.updatedAt).toLocaleString();
    if (key === "uuid") return row.id;
    return row.validationStatus.toLowerCase().replaceAll("_", " ");
  };
  const visible = layout.filter(c => c.visible || archivedView && c.key === "archivedAt");
  const review = jobs[0] ? describeChange(jobs[0].change) : null;
  const waiting = jobs.some(j => j.status === "waiting"), unknown = jobs.some(j => j.status === "unknown");
  return <>
    {!detail && <div className="material-table-toolbar">
      <details className="resource-properties"><summary>Properties</summary>
        {layout.map(column => <label key={column.key}><input type="checkbox" checked={column.visible || archivedView && column.key === "archivedAt"} disabled={archivedView && column.key === "archivedAt"} onChange={e => configure(layout.map(c => c.key === column.key ? { ...c, visible: e.target.checked } : c))} />{title(column.key)}</label>)}
      </details>
      <button className="button" disabled={active || lifecycleBusy || Boolean(identity)} onClick={refresh}>Refresh materials</button>
      {selectable && <span>{selectedRows.length} selected</span>}
      {publisher && onPreparePublication && <button className="button" disabled={active || lifecycleBusy || Boolean(identity) || !selectedRows.length || selectedRows.length > 100} onClick={() => onPreparePublication(selectedRows.map(row => ({ ...row })))}>Prepare selected for publication ({selectedRows.length})</button>}
    </div>}
    {!detail && editor && selectedRows.length > 0 && <fieldset className="material-bulk-bar" disabled={active || lifecycleBusy}><legend>Apply to {selectedRows.length} selected materials</legend>
      <label>Property<select value={bulkField} onChange={e => { const field = e.target.value as EditField; setBulkField(field); setBulkValue(bulkChoices(field)[0]?.value ?? ""); }}>
        {!archivedView && <option value="workflow_status">Status</option>}{manager && <>{!archivedView && <option value="checked_status">Checked</option>}<option value="is_published">Published</option>{role === "ADMIN" && <option value="is_archived">Archived</option>}<option value="project_id">Project</option><option value="assigned_processor_id">Processor</option></>}<option value="note">Note</option></select></label>
      <label>New value{bulkField === "note" ? <textarea value={bulkValue} maxLength={10000} onChange={e => setBulkValue(e.target.value)} /> : <select value={bulkValue} onChange={e => setBulkValue(e.target.value)}>{bulkChoices(bulkField).map(c => <option key={c.value} value={c.value}>{c.label}</option>)}</select>}</label>
      <button className="button button--primary" disabled={bulkField === "assigned_processor_id" && !bulkValue} onClick={() => prepare(selectedRows, bulkChange())}>Review bulk change</button>
    </fieldset>}
    {notice && <p role="status">{notice}</p>}
    {inline && pending && <p role="status">Saving {jobs[0]?.material.materialName}…</p>}
    {inline && jobs.some(j => j.status === "failed" || j.status === "unknown") && <div role="alert" className="form-error">
      {jobs[0].message}{unknown && <button className="button" disabled={pending} onClick={() => void run()}>Retry same request</button>}
    </div>}
    {detail ? <dl className="info-list material-property-editor">{columns.filter(([key]) => key !== "archivedAt" || rows[0].isArchived).map(([key, name]) => <div key={key}><dt>{name}</dt><dd>{renderCell(key, rows[0])}</dd></div>)}</dl> : <div className="table-card material-table material-table--editable" role="region" aria-label="Material results" tabIndex={0}>
      <table style={{ width: 356 + visible.reduce((n, c) => n + c.width, 0) }}><caption className="sr-only">Materials and production status</caption>
        <colgroup><col style={{ width: 40 }} /><col style={{ width: 76 }} /><col style={{ width: 240 }} />{visible.map(c => <col key={c.key} style={{ width: c.width }} />)}</colgroup>
        <thead><tr><th scope="col">{selectable && <input type="checkbox" aria-label="Select all visible materials" disabled={active || lifecycleBusy} checked={rows.length > 0 && selectedRows.length === rows.length} onChange={e => setSelected(new Set(e.target.checked ? rows.map(r => r.id) : []))} />}</th><th scope="col">Preview</th><th scope="col">Material</th>{visible.map(c => <th key={c.key} scope="col">{title(c.key)}</th>)}</tr></thead>
        <tbody>{rows.map(row => <tr key={row.id} className={selected.has(row.id) ? "is-selected" : ""}>
          <td>{selectable && <input type="checkbox" aria-label={`Select ${row.materialName}`} disabled={active || lifecycleBusy} checked={selected.has(row.id)} onChange={e => setSelected(old => { const next = new Set(old); if (e.target.checked) next.add(row.id); else next.delete(row.id); return next; })} />}</td>
          <td><MaterialThumbnail material={row} store={store} /></td><td><NavigationLink className="table-link" href={row.isArchived ? `/material-archives/${row.id}` : `/materials/${row.id}`} navigate={navigate}>{row.materialName}</NavigationLink><NavigationLink className="table-identity" href={row.isArchived ? `/material-archives/${row.id}` : `/materials/${row.id}`} navigate={navigate}>{row.technicalIdentity}</NavigationLink></td>
          {visible.map(c => <td key={c.key}>{renderCell(c.key, row)}</td>)}</tr>)}</tbody>
      </table>
    </div>}
    <dialog className="confirm-dialog material-bulk-dialog" ref={dialog} aria-labelledby="material-change-title" onCancel={e => { e.preventDefault(); close(); }}>
      <div className="confirm-dialog__body"><h2 id="material-change-title">Change {jobs.length} material{jobs.length === 1 ? "" : "s"}</h2>
        <p>Selected records are fixed for this operation. Each write checks that the material has not changed.</p>
        {review && <p className="note-text"><strong>{review[0]}</strong> → {review[1]}</p>}
        {jobs[0] && "note" in jobs[0].change && <p>This replaces the existing note on each selected material.</p>}
        {jobs[0] && "workflow_status" in jobs[0].change && jobs[0].change.workflow_status === "DONE" && <p>Done checks the linked folder and resets Checked to no. Missing metadata.txt is allowed when the folder is safe.</p>}
        {jobs[0] && "checked_status" in jobs[0].change && jobs[0].change.checked_status === "Correction" && <p>Correction returns the material to In progress.</p>}
        {jobs[0] && "is_published" in jobs[0].change && <p>Published records your manual evidence. It does not upload files.</p>}
        <p role="status">{jobs.filter(j => j.status === "saved").length} saved · {jobs.filter(j => j.status === "failed").length} rejected · {jobs.filter(j => j.status === "waiting").length} waiting{pending ? " · Saving…" : ""}</p>
        <ul className="bulk-report">{jobs.map(job => <li key={job.key}><strong>{job.material.materialName}</strong><span>{job.message ?? "Waiting"}</span></li>)}</ul>
        <div className="form-actions"><button className="button" disabled={pending || unknown} onClick={close}>{jobs.every(j => j.status === "waiting") ? "Cancel" : "Close report"}</button>
          {pending ? <button className="button" onClick={() => { stop.current = true; }}>Stop after current material</button> : (waiting || unknown) && <button className="button button--primary" onClick={() => void run()}>{unknown ? "Retry same request and continue" : "Apply change"}</button>}
        </div>
      </div>
    </dialog>
    <dialog className="material-identity-dialog" ref={identityDialog} aria-label="Change material identity" onCancel={e => { e.preventDefault(); if (identityBusy) return; identityDialog.current?.close(); setIdentity(undefined); returnFocus.current?.focus(); }}>
      {identity && <MaterialIdentityPanel key={`${identity.material.id}:${identity.brand}:${identity.category}`} material={identity.material} client={client} initialBrand={identity.brand} initialCategory={identity.category} onBusyChange={setIdentityBusy} onChanged={async () => {
        try { const row = await client.getMaterial(identity.material.id); setOverrides(old => ({ ...old, [row.id]: row })); onMaterialChanged?.(row); setIdentity(current => current ? { ...current, material: row } : current); return true; } catch { return false; }
      }} />}
      <button disabled={identityBusy} className="button" onClick={() => { identityDialog.current?.close(); setIdentity(undefined); returnFocus.current?.focus(); }}>Back to materials</button>
    </dialog>
  </>;
}
