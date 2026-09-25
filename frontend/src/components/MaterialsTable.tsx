import { useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import type { GalleryStore } from "../api/galleryStore";
import { type InternalUser, type Material, checkedStatuses } from "../api/materialDto";
import { materialTableClient, type TableChange } from "../api/materialTableClient";
import { useSession } from "../auth/context";
import { sessionGeneration } from "../auth/sessionTransport";
import { categoryLabel, materialCategories } from "../data/materialCategories";
import type { Project, PublishedBrand } from "../types";
import { MaterialIdentityPanel } from "./MaterialIdentityPanel";
import { MaterialThumbnail } from "./MaterialsGrid";
import { NavigationLink } from "./NavigationLink";

const columns = [
  ["project", "Project", 180], ["brand", "Published brand", 180], ["category", "Category", 225],
  ["status", "Status", 140], ["checked", "Checked", 140], ["published", "Published", 100],
  ["processor", "Processor", 180], ["note", "Note", 240], ["folder", "Folder path", 340],
  ["number", "Number", 90], ["created", "Created", 200], ["updated", "Updated", 200],
  ["uuid", "Internal UUID", 310], ["technical", "File check", 145],
] as const;
type Column = typeof columns[number][0];
type Layout = { key: Column; visible: boolean; width: number }[];
const defaultLayout = (): Layout => columns.map(([key, , width], index) => ({ key, width, visible: index < 8 }));
function readLayout(): Layout {
  try {
    const value: unknown = JSON.parse(localStorage.getItem("materials.columns.v1") ?? "null");
    if (Array.isArray(value) && value.length === columns.length && new Set(value.map(v => v.key)).size === columns.length && value.every(v => columns.some(([key]) => key === v.key) && typeof v.visible === "boolean" && Number.isInteger(v.width) && v.width >= 90 && v.width <= 600)) return value;
  } catch { /* Invalid/disabled storage falls back to defaults. */ }
  return defaultLayout();
}
type EditField = "workflow_status" | "checked_status" | "is_published" | "project_id" | "assigned_processor_id" | "note";
type Job = { material: Material; change: TableChange; key: string; status: "waiting" | "saved" | "failed" | "unknown" | "stopped"; message?: string };
type Props = { materials: Material[]; store: GalleryStore; client: ApiClient; projects: Project[]; brands: PublishedBrand[]; users: InternalUser[];
  navigate: (path: string) => void; refresh: () => void; onBusyChange: (busy: boolean) => void };

function NoteCell({ material, disabled, save }: { material: Material; disabled: boolean; save: (change: TableChange) => void }) {
  const [draft, setDraft] = useState(material.note ?? "");
  return <div className="table-note"><textarea aria-label={`Note for ${material.materialName}`} rows={2} maxLength={10000} value={draft} disabled={disabled}
    placeholder="Add a note…" onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === "Escape") setDraft(material.note ?? ""); if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save({ note: draft || null }); } }} />
    {draft !== (material.note ?? "") && <button className="button" disabled={disabled} onClick={() => save({ note: draft || null })}>Save note</button>}</div>;
}

export function MaterialsTable({ materials, store, client, projects, brands, users, navigate, refresh, onBusyChange }: Props) {
  const role = useSession()?.session.user.role;
  const manager = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const editor = manager || role === "PROCESSOR";
  const [layout, setLayout] = useState(readLayout);
  const [overrides, setOverrides] = useState<Record<string, Material>>({});
  const rows = materials.map(row => overrides[row.id] ?? row);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkField, setBulkField] = useState<EditField>("workflow_status");
  const [bulkValue, setBulkValue] = useState("DONE");
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
  useEffect(() => { alive.current = true; return () => { alive.current = false; stop.current = true; }; }, []);
  useEffect(() => {
    onBusyChange(active || Boolean(identity));
    return () => onBusyChange(false);
  }, [active, identity, onBusyChange]);
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
      try {
        const saved = await materialTableClient.update(job.material, job.change, job.key);
        if (!alive.current || generation !== sessionGeneration()) break;
        setOverrides(old => ({ ...old, [saved.id]: saved }));
        updateJob(i, { status: "saved", message: "Saved" });
      } catch (error) {
        if (!alive.current) break;
        if (job.status !== "unknown" && error instanceof ApiError && (error.status >= 400 && error.status < 500 || error.code === "TABLE_PREFLIGHT_UNAVAILABLE")) {
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
  const prepare = (targets: Material[], change: TableChange, immediate = false) => {
    if (sending.current || active) return;
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
    if (field === "is_published") return [{ value: "true", label: "Yes" }, { value: "false", label: "No" }];
    if (field === "project_id") return [{ value: "", label: "No project assigned" }, ...projects.map(p => ({ value: p.id, label: p.name }))];
    if (field === "assigned_processor_id") return users.filter(u => u.isActive && u.role === "PROCESSOR").map(u => ({ value: u.id, label: u.displayName }));
    return [];
  };
  const bulkChange = (): TableChange => ({ [bulkField]: bulkField === "is_published" ? bulkValue === "true" : bulkValue || null }) as TableChange;
  const describeChange = (change: TableChange): [string, string] => {
    if ("workflow_status" in change) return ["Status", change.workflow_status === "DONE" ? "Done" : "In progress"];
    if ("checked_status" in change) return ["Checked", change.checked_status];
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
  const close = () => { if (pending || jobs.some(j => j.status === "unknown")) return; dialog.current?.close(); setActive(false); returnFocus.current?.focus(); };
  const title = (key: Column) => columns.find(([value]) => value === key)![1];
  const renderCell = (key: Column, row: Material) => {
    const save = (change: TableChange) => edit(row, change);
    const disable = active || Boolean(identity);
    if (key === "project") return manager ? <select aria-label={`Project for ${row.materialName}`} disabled={disable} value={row.projectId ?? ""} onChange={e => save({ project_id: e.target.value || null })}>
      <option value="">No project assigned</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select> : projects.find(p => p.id === row.projectId)?.name ?? row.projectId ?? "No project assigned";
    if (key === "brand") return manager ? <select aria-label={`Published brand for ${row.materialName}`} disabled={disable} value={row.publishedBrandId} onChange={e => openIdentity(row, { brand: e.target.value })}>
      {brands.filter(b => b.isActive || b.id === row.publishedBrandId).map(b => <option key={b.id} value={b.id}>{b.name}</option>)}</select> : brands.find(b => b.id === row.publishedBrandId)?.name ?? row.publishedBrandId;
    if (key === "category") return manager ? <select aria-label={`Category for ${row.materialName}`} disabled={disable} value={row.mainCategoryCode} onChange={e => openIdentity(row, { category: e.target.value })}>
      {!materialCategories.some(c => c.code === row.mainCategoryCode) && <option value={row.mainCategoryCode}>{categoryLabel(row.mainCategoryCode)}</option>}
      {materialCategories.map(c => <option key={c.code} value={c.code}>{categoryLabel(c.code)}</option>)}</select> : categoryLabel(row.mainCategoryCode);
    if (key === "status") return editor ? <select aria-label={`Status for ${row.materialName}`} disabled={disable} value={row.workflowStatus} onChange={e => save({ workflow_status: e.target.value as Material["workflowStatus"] })}>
      <option value="IN_PROGRESS">In progress</option><option value="DONE">Done</option></select> : row.workflowStatus === "DONE" ? "Done" : "In progress";
    if (key === "checked") return manager ? <select aria-label={`Checked for ${row.materialName}`} disabled={disable} value={row.checkedStatus} onChange={e => save({ checked_status: e.target.value as Material["checkedStatus"] })}>
      {checkedStatuses.map(value => <option key={value} value={value}>{value}</option>)}</select> : row.checkedStatus;
    if (key === "published") return <input type="checkbox" aria-label={`Published for ${row.materialName}`} disabled={!manager || disable} checked={row.isPublished} onChange={e => save({ is_published: e.target.checked })} />;
    if (key === "processor") return manager ? <select aria-label={`Processor for ${row.materialName}`} disabled={disable} value={row.assignedProcessorId} onChange={e => save({ assigned_processor_id: e.target.value })}>
      {users.filter(u => u.id === row.assignedProcessorId || u.isActive && u.role === "PROCESSOR").map(u => <option key={u.id} value={u.id}>{u.displayName}{!u.isActive ? " (inactive)" : ""}</option>)}</select> : users.find(u => u.id === row.assignedProcessorId)?.displayName ?? row.assignedProcessorId;
    if (key === "note") return editor ? <NoteCell key={`${row.id}:${row.note ?? ""}`} material={row} disabled={disable} save={save} /> : <span className="note-text">{row.note ?? "—"}</span>;
    if (key === "folder") return <span className="material-folder-path">{row.folderPath ?? "No folder linked"}</span>;
    if (key === "number") return String(row.sequenceNumber).padStart(4, "0");
    if (key === "created") return new Date(row.createdAt).toLocaleString();
    if (key === "updated") return new Date(row.updatedAt).toLocaleString();
    if (key === "uuid") return row.id;
    return row.validationStatus.toLowerCase().replaceAll("_", " ");
  };
  const visible = layout.filter(c => c.visible);
  const review = jobs[0] ? describeChange(jobs[0].change) : null;
  const waiting = jobs.some(j => j.status === "waiting"), unknown = jobs.some(j => j.status === "unknown");
  return <>
    <div className="material-table-toolbar">
      <details className="table-properties"><summary className="button">Properties · {visible.length + 2}</summary><div className="panel">
        <p>Preview and material stay pinned. Choose, order and resize the other columns.</p>
        {layout.map((column, index) => <div className="column-setting" key={column.key}><label><input type="checkbox" checked={column.visible} onChange={e => configure(layout.map(c => c.key === column.key ? { ...c, visible: e.target.checked } : c))} />{title(column.key)}</label>
          <input type="number" aria-label={`Width of ${title(column.key)}`} min={90} max={600} value={column.width} onChange={e => { const n = Number(e.target.value); if (n >= 90 && n <= 600) configure(layout.map(c => c.key === column.key ? { ...c, width: n } : c)); }} />
          <button className="button" aria-label={`Move ${title(column.key)} left`} disabled={index === 0} onClick={() => { const next = [...layout]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; configure(next); }}>←</button>
          <button className="button" aria-label={`Move ${title(column.key)} right`} disabled={index === layout.length - 1} onClick={() => { const next = [...layout]; [next[index + 1], next[index]] = [next[index], next[index + 1]]; configure(next); }}>→</button></div>)}
        <button className="button" onClick={() => configure(defaultLayout())}>Reset columns</button>
      </div></details>
      <button className="button" disabled={active || Boolean(identity)} onClick={refresh}>Refresh materials</button>
      {editor && <><button className="button" disabled={active} onClick={() => setSelected(new Set(rows.map(r => r.id)))}>Select all filtered ({rows.length})</button>
        <button className="button" disabled={active || !selectedRows.length} onClick={() => setSelected(new Set())}>Clear selection</button><span>{selectedRows.length} selected</span></>}
    </div>
    {editor && selectedRows.length > 0 && <fieldset className="material-bulk-bar" disabled={active}><legend>Apply to {selectedRows.length} selected materials</legend>
      <label>Property<select value={bulkField} onChange={e => { const field = e.target.value as EditField; setBulkField(field); setBulkValue(bulkChoices(field)[0]?.value ?? ""); }}>
        <option value="workflow_status">Status</option>{manager && <><option value="checked_status">Checked</option><option value="is_published">Published</option><option value="project_id">Project</option><option value="assigned_processor_id">Processor</option></>}<option value="note">Note</option></select></label>
      <label>New value{bulkField === "note" ? <textarea value={bulkValue} maxLength={10000} onChange={e => setBulkValue(e.target.value)} /> : <select value={bulkValue} onChange={e => setBulkValue(e.target.value)}>{bulkChoices(bulkField).map(c => <option key={c.value} value={c.value}>{c.label}</option>)}</select>}</label>
      <button className="button button--primary" disabled={bulkField === "assigned_processor_id" && !bulkValue} onClick={() => prepare(selectedRows, bulkChange())}>Review bulk change</button>
    </fieldset>}
    {notice && <p role="status">{notice}</p>}
    {inline && pending && <p role="status">Saving {jobs[0]?.material.materialName}…</p>}
    {inline && jobs.some(j => j.status === "failed" || j.status === "unknown") && <div role="alert" className="form-error">
      {jobs[0].message}{unknown && <button className="button" disabled={pending} onClick={() => void run()}>Retry same request</button>}
    </div>}
    <div className="table-card material-table material-table--editable" role="region" aria-label="Material results" tabIndex={0}>
      <table style={{ width: 356 + visible.reduce((n, c) => n + c.width, 0) }}><caption className="sr-only">Materials and production status</caption>
        <colgroup><col style={{ width: 40 }} /><col style={{ width: 76 }} /><col style={{ width: 240 }} />{visible.map(c => <col key={c.key} style={{ width: c.width }} />)}</colgroup>
        <thead><tr><th scope="col">{editor && <input type="checkbox" aria-label="Select all visible materials" disabled={active} checked={rows.length > 0 && selectedRows.length === rows.length} onChange={e => setSelected(new Set(e.target.checked ? rows.map(r => r.id) : []))} />}</th><th scope="col">Preview</th><th scope="col">Material</th>{visible.map(c => <th key={c.key} scope="col">{title(c.key)}</th>)}</tr></thead>
        <tbody>{rows.map(row => <tr key={row.id} className={selected.has(row.id) ? "is-selected" : ""}>
          <td>{editor && <input type="checkbox" aria-label={`Select ${row.materialName}`} disabled={active} checked={selected.has(row.id)} onChange={e => setSelected(old => { const next = new Set(old); if (e.target.checked) next.add(row.id); else next.delete(row.id); return next; })} />}</td>
          <td><MaterialThumbnail material={row} store={store} /></td><td><NavigationLink className="table-link" href={`/materials/${row.id}`} navigate={navigate}>{row.materialName}</NavigationLink><NavigationLink className="table-identity" href={`/materials/${row.id}`} navigate={navigate}>{row.technicalIdentity}</NavigationLink></td>
          {visible.map(c => <td key={c.key}>{renderCell(c.key, row)}</td>)}</tr>)}</tbody>
      </table>
    </div>
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
        try { const row = await client.getMaterial(identity.material.id); setOverrides(old => ({ ...old, [row.id]: row })); setIdentity(current => current ? { ...current, material: row } : current); return true; } catch { return false; }
      }} />}
      <button disabled={identityBusy} className="button" onClick={() => { identityDialog.current?.close(); setIdentity(undefined); returnFocus.current?.focus(); }}>Back to materials</button>
    </dialog>
  </>;
}
