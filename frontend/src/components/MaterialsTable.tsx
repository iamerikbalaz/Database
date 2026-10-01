import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
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
import { highlightMaterial, isInteractiveTarget, type HighlightState } from "./materialHighlight";
import { DatabaseTableViewport } from "./DatabaseTableViewport";
import { Icon } from "./Icon";
import { MaterialNameDialog } from "./MaterialNameDialog";
import { MaterialPreviewStrip } from "./MaterialPreviewStrip";
import { PreviewEditDialog, type PreviewEditSelection } from "./PreviewEditDialog";
import { identityClient, type IdentityConfirmation } from "../api/identityClient";
import { MaterialBulkNamesDialog } from "./MaterialBulkNamesDialog";

const columns = [
  ["project", "Order", 180], ["brand", "Customer", 180], ["category", "Category", 225],
  ["status", "Status", 140], ["checked", "Checked", 140], ["published", "Published", 100],
  ["archived", "Archived", 110], ["archivedAt", "Archive date", 200],
  ["processor", "Processor", 180], ["note", "Note", 240], ["folder", "Folder path", 340],
  ["number", "Number", 90], ["created", "Created", 200], ["updated", "Updated", 200],
  ["uuid", "Internal UUID", 310], ["technical", "Automatic file check", 180],
] as const;
type Column = typeof columns[number][0];
type Layout = { key: Column; visible: boolean; width: number }[];
const defaultLayout = (): Layout => columns.map(([key, , width]) => ({ key, width, visible: ["project", "brand", "category", "status", "checked", "technical", "published", "archived", "processor", "note"].includes(key) }));
function readLayout(): Layout {
  try {
    const value: unknown = JSON.parse(localStorage.getItem("materials.columns.v1") ?? "null");
    if (Array.isArray(value) && value.every(v => v && columns.some(([key]) => key === v.key) && typeof v.visible === "boolean")) return defaultLayout().map(column => ({ ...column, visible: value.find(v => v.key === column.key)?.visible ?? column.visible }));
  } catch { /* Invalid/disabled storage falls back to defaults. */ }
  return defaultLayout();
}
type EditField = "is_archived" | "workflow_status" | "checked_status" | "is_published" | "project_id" | "assigned_processor_id" | "note" | "main_category_code";
type Change = TableChange | { is_archived: boolean };
type Job = { material: Material; change: Change; lifecycle?: { preview: ArchivePreview; body: LifecycleRequest }; identity?: IdentityConfirmation; identityOperation?: string; key: string; status: "waiting" | "saved" | "failed" | "unknown" | "stopped"; message?: string };
type Props = { materials: Material[]; store: GalleryStore; client: ApiClient; projects: Project[]; brands: PublishedBrand[]; users: InternalUser[];
  categories?: { code: string; value: string }[];
  categoryLabels?: { code: string; value: string; aliases?: string[] }[];
  navigate: (path: string) => void; refresh: () => void; onBusyChange: (busy: boolean) => void;
  onPreparePublication?: (materials: Material[]) => void; detail?: boolean; onMaterialChanged?: (material: Material) => void;
  selection?: { ids: Set<string>; change: (ids: Set<string>) => void };
  onCheckSelected?: ((materials: Material[]) => void) | undefined; checkActionsRef?: (node: HTMLDivElement | null) => void; operationBusy?: boolean; scrollMode?: "page" | "contained" };

function NoteCell({ material, disabled, save }: { material: Material; disabled: boolean; save: (change: TableChange) => void }) {
  const [draft, setDraft] = useState(material.note ?? "");
  return <div className="table-note"><textarea aria-label={`Note for ${material.materialName}`} rows={2} maxLength={10000} value={draft} disabled={disabled}
    placeholder="Add a note…" onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === "Escape") setDraft(material.note ?? ""); if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save({ note: draft || null }); } }} />
    {draft !== (material.note ?? "") && <button className="button" disabled={disabled} onClick={() => save({ note: draft || null })}>Save note</button>}</div>;
}

export function MaterialsTable({ materials, store, client, projects, brands, users, categories = materialCategories, categoryLabels = categories, navigate, refresh, onBusyChange, onPreparePublication, detail = false, onMaterialChanged, selection, onCheckSelected, checkActionsRef, operationBusy = false, scrollMode = "page" }: Props) {
  const actor = useSession()?.session.user;
  const role = actor?.role;
  const manager = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const editor = manager || role === "PROCESSOR";
  const publisher = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const selectable = editor || (publisher && Boolean(onPreparePublication));
  const [layout, setLayout] = useState(readLayout);
  const [overrides, setOverrides] = useState<Record<string, Material>>({});
  const rows = materials.map(row => overrides[row.id] && overrides[row.id].updatedAt > row.updatedAt ? overrides[row.id] : row);
  const archivedView = materials.every(row => row.isArchived);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [localSelected, setLocalSelected] = useState<Set<string>>(new Set());
  const selected = selection?.ids ?? localSelected;
  const setSelected = selection?.change ?? setLocalSelected;
  const [highlight, setHighlight] = useState<HighlightState>({ ids: new Set(), anchor: null });
  const visibleKey = materials.map(row => row.id).join(":");
  const [previousVisibleKey, setPreviousVisibleKey] = useState(visibleKey);
  if (previousVisibleKey !== visibleKey) {
    setPreviousVisibleKey(visibleKey);
    const visible = new Set(materials.map(row => row.id));
    setHighlight({ ids: new Set([...highlight.ids].filter(id => visible.has(id))), anchor: highlight.anchor && visible.has(highlight.anchor) ? highlight.anchor : null });
    if (!selection) setLocalSelected(new Set([...localSelected].filter(id => visible.has(id))));
  }
  const [bulkField, setBulkField] = useState<EditField>(materials.every(row => row.isArchived) ? "note" : "workflow_status");
  const [bulkValue, setBulkValue] = useState(materials.every(row => row.isArchived) ? "" : "DONE");
  const [jobs, setJobs] = useState<Job[]>([]);
  const jobsRef = useRef<Job[]>([]);
  const [pending, setPending] = useState(false);
  const [internallyActive, setActive] = useState(false);
  const active = internallyActive || operationBusy;
  const [inline, setInline] = useState(false);
  const inlineRef = useRef(false);
  const [notice, setNotice] = useState("");
  const [identityBusy, setIdentityBusy] = useState(false);
  const [identity, setIdentity] = useState<{ material: Material; brand?: string; category?: string }>();
  const [rename, setRename] = useState<Material | null>(null), [previewEdit, setPreviewEdit] = useState<PreviewEditSelection | null>(null);
  const [bulkNames, setBulkNames] = useState<Material[] | null>(null), namesChanged = useRef(false);
  const previewsChanged = useRef(false);
  const [expanded, setExpanded] = useState(false), [previewCounts, setPreviewCounts] = useState<Record<string, number>>({}), [previewEpoch, setPreviewEpoch] = useState(0);
  const countPreviews = useCallback((id: string, count: number) => setPreviewCounts(current => current[id] === count ? current : { ...current, [id]: count }), []);
  const editDialogOpen = Boolean(rename || previewEdit || bulkNames);
  const dialog = useRef<HTMLDialogElement>(null), identityDialog = useRef<HTMLDialogElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const alive = useRef(true), sending = useRef(false), stop = useRef(false);
  const generation = sessionGeneration();
  useNavigationGuard(() => sending.current || jobsRef.current.some(job => job.status === "unknown") || identityBusy);
  useEffect(() => { alive.current = true; return () => { alive.current = false; stop.current = true; }; }, []);
  useEffect(() => {
    onBusyChange(internallyActive || lifecycleBusy || Boolean(identity) || editDialogOpen);
    return () => onBusyChange(false);
  }, [internallyActive, lifecycleBusy, identity, editDialogOpen, onBusyChange]);
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
      let mutationStarted = Boolean(job.lifecycle || job.identity);
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
        if ("main_category_code" in job.change && job.material.folderPath) {
          if (job.material.mainCategoryCode === job.change.main_category_code) { updateJob(i, { status: "saved", message: "Already saved" }); continue; }
          let packet = job.identity;
          if (!packet) {
            if (job.material.isPublished || job.material.isArchived || job.material.workflowStatus !== "IN_PROGRESS") throw new ApiError(409, "Clear Published and set Status to In progress before changing Main category and its source folder.");
            const current = await client.getMaterial(job.material.id);
            if (current.updatedAt !== job.material.updatedAt) throw new ApiError(409, "Material changed. Refresh before changing Main category.");
            const target = { target_brand_id: job.material.publishedBrandId, main_category_code: job.change.main_category_code, target_parent: job.material.folderPath.split("/").slice(0, -1).join("/") };
            const plan = await identityClient.plan(job.material.id, target);
            if (!alive.current || generation !== sessionGeneration()) break;
            if (!plan.ready) throw new ApiError(409, plan.errors.map(item => `${item.code}: ${item.path}`).join(" · "));
            packet = { ...target, expected_generation: plan.generation, expected_proposal_hash: plan.hash, idempotency_key: job.key, reason: "Bulk Main category change", warnings_acknowledged: true };
            updateJob(i, { identity: packet });
          }
          mutationStarted = true;
          const operation = job.identityOperation ? await identityClient.resume(job.material.id, job.identityOperation) : await identityClient.confirm(job.material.id, packet);
          if (!alive.current || generation !== sessionGeneration()) break;
          if (operation.status === "RUNNING" || operation.status === "RECOVERY_REQUIRED") { updateJob(i, { status: "unknown", identityOperation: operation.id, message: "Folder changes need recovery. Retry this recorded operation." }); break; }
          if (operation.status !== "COMPLETED") { updateJob(i, { status: "failed", message: "Folder change was rejected or rolled back." }); continue; }
          const updated = await client.getMaterial(job.material.id);
          if (!alive.current || generation !== sessionGeneration()) break;
          store.forget(job.material.id, job.material.folderPath); setPreviewEpoch(value => value + 1);
          setOverrides(old => ({ ...old, [updated.id]: updated })); onMaterialChanged?.(updated);
          updateJob(i, { status: "saved", message: "Main category and source folder updated" }); continue;
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
          updateJob(i, { status: "failed", message: error instanceof ApiError && error.status === 409 ? error.message : "Current material eligibility could not be verified. Nothing was changed." });
          if (!(error instanceof ApiError) || error.status !== 409) stop.current = true;
        } else if (job.status !== "unknown" && !job.identity && !jobsRef.current[i].identity && error instanceof ApiError && (error.status >= 400 && error.status < 500 || error.code === "TABLE_PREFLIGHT_UNAVAILABLE")) {
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
    if (field === "project_id") return [{ value: "", label: "No order assigned" }, ...projects.map(p => ({ value: p.id, label: p.name }))];
    if (field === "assigned_processor_id") return users.filter(u => u.isActive && u.role === "PROCESSOR").map(u => ({ value: u.id, label: u.displayName }));
    if (field === "main_category_code") return categories.map(c => ({ value: c.code, label: categoryLabel(c.code, categoryLabels) }));
    return [];
  };
  const bulkChange = (): Change => ({ [bulkField]: bulkField === "is_published" || bulkField === "is_archived" ? bulkValue === "true" : bulkValue || null }) as Change;
  const describeChange = (change: Change): [string, string] => {
    if ("workflow_status" in change) return ["Status", change.workflow_status === "DONE" ? "Done" : "In progress"];
    if ("checked_status" in change) return ["Checked", change.checked_status];
    if ("is_archived" in change) return ["Archived", change.is_archived ? "Yes" : "No"];
    if ("is_published" in change) return ["Published", change.is_published ? "Yes" : "No"];
    if ("project_id" in change) return ["Order", change.project_id === null ? "No order assigned" : projects.find(p => p.id === change.project_id)?.name ?? change.project_id];
    if ("assigned_processor_id" in change) return ["Processor", users.find(u => u.id === change.assigned_processor_id)?.displayName ?? change.assigned_processor_id];
    if ("published_brand_id" in change) return ["Customer", brands.find(b => b.id === change.published_brand_id)?.name ?? change.published_brand_id];
    if ("main_category_code" in change) return ["Category", categoryLabel(change.main_category_code, categoryLabels)];
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
    if (key === "project") return manager ? <select aria-label={`Order for ${row.materialName}`} disabled={disable} value={row.projectId ?? ""} onChange={e => save({ project_id: e.target.value || null })}>
      <option value="">No order assigned</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select> : projects.find(p => p.id === row.projectId)?.name ?? row.projectId ?? "No order assigned";
    if (key === "brand") return manager ? <select aria-label={`Customer for ${row.materialName}`} disabled={productionDisabled} value={row.publishedBrandId} onChange={e => openIdentity(row, { brand: e.target.value })}>
      {brands.filter(b => b.isActive || b.id === row.publishedBrandId).map(b => <option key={b.id} value={b.id}>{b.name}</option>)}</select> : brands.find(b => b.id === row.publishedBrandId)?.name ?? row.publishedBrandId;
    if (key === "category") return manager ? <select aria-label={`Category for ${row.materialName}`} disabled={productionDisabled} value={row.mainCategoryCode} onChange={e => openIdentity(row, { category: e.target.value })}>
      {!categories.some(c => c.code === row.mainCategoryCode) && <option value={row.mainCategoryCode}>{categoryLabel(row.mainCategoryCode, categoryLabels)}</option>}
      {categories.map(c => <option key={c.code} value={c.code}>{categoryLabel(c.code, categoryLabels)}</option>)}</select> : categoryLabel(row.mainCategoryCode, categoryLabels);
    if (key === "status") return editor ? <select aria-label={`Status for ${row.materialName}`} disabled={productionDisabled} value={row.workflowStatus} onChange={e => save({ workflow_status: e.target.value as Material["workflowStatus"] })}>
      <option value="IN_PROGRESS">In progress</option><option value="DONE">Done</option></select> : row.workflowStatus === "DONE" ? "Done" : "In progress";
    if (key === "checked") return manager ? <select aria-label={`Checked for ${row.materialName}`} disabled={productionDisabled} value={row.checkedStatus} onChange={e => save({ checked_status: e.target.value as Material["checkedStatus"] })}>
      {checkedStatuses.map(value => <option key={value} value={value}>{value}</option>)}</select> : row.checkedStatus;
    if (key === "published") return <input type="checkbox" aria-label={`Published for ${row.materialName}`} disabled={!manager || disable} checked={row.isPublished} onChange={e => save({ is_published: e.target.checked })} />;
    if (key === "archived") return <MaterialLifecyclePanel materialId={row.id} archived={row.isArchived} label={`Archived for ${row.materialName}`} navigate={navigate} disabled={active || lifecycleBusy || Boolean(identity)} onBusyChange={setLifecycleBusy} onApplied={() => { refresh(); }} />;
    if (key === "archivedAt") return row.archivedAt ? new Date(row.archivedAt).toLocaleString() : "—";
    if (key === "processor") return manager ? <select aria-label={`Processor for ${row.materialName}`} disabled={disable} value={row.assignedProcessorId} onChange={e => save({ assigned_processor_id: e.target.value })}>
      {users.filter(u => u.id === row.assignedProcessorId || u.isActive && u.role === "PROCESSOR").map(u => <option key={u.id} value={u.id} disabled={!u.isActive || u.role !== "PROCESSOR"}>{u.displayName}{!u.isActive ? " (inactive)" : ""}</option>)}</select> : users.find(u => u.id === row.assignedProcessorId)?.displayName ?? row.assignedProcessorId;
    if (key === "note") return editor ? <NoteCell key={`${row.id}:${row.note ?? ""}`} material={row} disabled={disable} save={save} /> : <span className="note-text">{row.note ?? "—"}</span>;
    if (key === "folder") return <span className="material-folder-path">{row.folderPath ? validateFolderPath(row.folderPath).error ? "Unavailable (unsafe path hidden)" : row.folderPath : "No folder linked"}</span>;
    if (key === "number") return String(row.sequenceNumber).padStart(4, "0");
    if (key === "created") return new Date(row.createdAt).toLocaleString();
    if (key === "updated") return new Date(row.updatedAt).toLocaleString();
    if (key === "uuid") return row.id;
    const status = row.automaticFileCheckStatus ?? "NOT_CHECKED";
    return <span className={`automatic-file-check automatic-file-check--${status.toLowerCase()}`}>{status === "NOT_CHECKED" ? "not checked" : status === "OK" ? "OK" : "issues"}</span>;
  };
  const numberColumn = layout.find(c => c.key === "number" && c.visible);
  const visible = layout.filter(c => c.key !== "number" && (c.visible || archivedView && c.key === "archivedAt"));
  const previewWidth = expanded ? Math.max(160, ...rows.map(row => (previewCounts[row.id] ?? 1) * 122 + 6)) : 96;
  const review = jobs[0] ? describeChange(jobs[0].change) : null;
  const waiting = jobs.some(j => j.status === "waiting"), unknown = jobs.some(j => j.status === "unknown");
  return <div className={detail ? "material-detail-properties" : `resource-database materials-database${scrollMode === "contained" ? " resource-database--contained" : ""}`}>
    {!detail && <div className="material-table-toolbar resource-table-toolbar">
      {selectable && <button className="button" disabled={active || lifecycleBusy || Boolean(identity) || !rows.some(row => highlight.ids.has(row.id))}
        onClick={() => setSelected(new Set([...selectedRows.map(row => row.id), ...rows.filter(row => highlight.ids.has(row.id)).map(row => row.id)]))}>Select highlighted ({rows.filter(row => highlight.ids.has(row.id)).length})</button>}
      {selectable && <span>{selectedRows.length} selected</span>}
      <details className="resource-properties"><summary>Properties</summary><div className="resource-property-options">
        {layout.map(column => <label key={column.key}><input type="checkbox" checked={column.visible || archivedView && column.key === "archivedAt"} disabled={archivedView && column.key === "archivedAt"} onChange={e => configure(layout.map(c => c.key === column.key ? { ...c, visible: e.target.checked } : c))} />{title(column.key)}</label>)}
      </div></details>
      <div className="materials-table-actions">
      <button className="button resource-table-refresh" aria-label="Refresh materials" title="Refresh materials" disabled={active || lifecycleBusy || Boolean(identity)} onClick={refresh}><Icon name="refresh" size={20} /></button>
      </div>
    </div>}
    {!detail && editor && selectedRows.length > 0 && <fieldset className="material-bulk-bar" disabled={internallyActive || lifecycleBusy}><legend>Apply to {selectedRows.length} selected materials</legend>
      <label>Property<select disabled={active || editDialogOpen} value={bulkField} onChange={e => { const field = e.target.value as EditField; setBulkField(field); setBulkValue(bulkChoices(field)[0]?.value ?? ""); }}>
        {!archivedView && <option value="workflow_status">Status</option>}{manager && <>{!archivedView && <><option value="checked_status">Checked</option><option value="main_category_code">Main category</option></>}<option value="is_published">Published</option>{role === "ADMIN" && <option value="is_archived">Archived</option>}<option value="project_id">Order</option><option value="assigned_processor_id">Processor</option></>}<option value="note">Note</option></select></label>
      <label>New value{bulkField === "note" ? <textarea disabled={active || editDialogOpen} value={bulkValue} maxLength={10000} onChange={e => setBulkValue(e.target.value)} /> : <select disabled={active || editDialogOpen} value={bulkValue} onChange={e => setBulkValue(e.target.value)}>{bulkChoices(bulkField).map(c => <option key={c.value} value={c.value}>{c.label}</option>)}</select>}</label>
      <button className="button button--primary" disabled={active || editDialogOpen || (bulkField === "assigned_processor_id" || bulkField === "main_category_code") && !bulkValue} onClick={() => prepare(selectedRows, bulkChange())}>Review bulk change</button>
      <div className="material-bulk-actions">
        {manager && !archivedView && <button className="button" disabled={active || lifecycleBusy || Boolean(identity) || editDialogOpen || selectedRows.length > 100} onClick={() => setBulkNames(selectedRows.map(row => ({ ...row })))}>Edit names</button>}
        {!archivedView && <button className="button" disabled={active || lifecycleBusy || Boolean(identity) || editDialogOpen || selectedRows.length > 100} onClick={() => setPreviewEdit({ materials: selectedRows.map(row => ({ ...row })), action: "BULK" })}>Edit previews</button>}
        {checkActionsRef && <div className="material-check-actions-slot" ref={checkActionsRef} />}
        {onCheckSelected && <button className="button" disabled={active || lifecycleBusy || Boolean(identity) || selectedRows.length > 100} onClick={() => onCheckSelected(selectedRows.map(row => ({ ...row })))}>Auto-check selected materials ({selectedRows.length})</button>}
        {publisher && onPreparePublication && <button className="button" disabled={active || lifecycleBusy || Boolean(identity) || selectedRows.length > 100} onClick={() => onPreparePublication(selectedRows.map(row => ({ ...row })))}>Prepare selected for publication ({selectedRows.length})</button>}
      </div>
    </fieldset>}
    {notice && <p role="status">{notice}</p>}
    {inline && pending && <p role="status">Saving {jobs[0]?.material.materialName}…</p>}
    {inline && jobs.some(j => j.status === "failed" || j.status === "unknown") && <div role="alert" className="form-error">
      {jobs[0].message}{unknown && <button className="button" disabled={pending} onClick={() => void run()}>Retry same request</button>}
    </div>}
    {detail ? <dl className="info-list material-property-editor">{columns.filter(([key]) => key !== "archivedAt" || rows[0].isArchived).map(([key, name]) => <div key={key}><dt>{name}</dt><dd>{renderCell(key, rows[0])}</dd></div>)}</dl> : <DatabaseTableViewport scrollMode={scrollMode} className={`table-card material-table material-table--editable${expanded ? " material-table--previews-expanded" : ""}`} label="Material results">
      <table style={{ width: 280 + previewWidth + (numberColumn?.width ?? 0) + visible.reduce((n, c) => n + c.width, 0), "--material-preview-width": `${previewWidth}px`, "--material-name-left": `${40 + previewWidth + (numberColumn?.width ?? 0)}px` } as CSSProperties}><caption className="sr-only">Materials and production status</caption>
        <colgroup><col style={{ width: 40 }} /><col style={{ width: previewWidth }} />{numberColumn && <col style={{ width: numberColumn.width }} />}<col style={{ width: 240 }} />{visible.map(c => <col key={c.key} style={{ width: c.width }} />)}</colgroup>
        <thead><tr><th scope="col">{selectable && <input type="checkbox" aria-label="Select all visible materials" disabled={active || lifecycleBusy} checked={rows.length > 0 && selectedRows.length === rows.length} onChange={e => setSelected(new Set(e.target.checked ? rows.map(r => r.id) : []))} />}</th><th scope="col"><button className="preview-column-toggle" aria-label={expanded ? "Collapse previews" : "Expand previews"} aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>Preview<Icon name="arrow" size={12} /></button></th>{numberColumn && <th scope="col">Number</th>}<th scope="col" className="material-name-column">Material</th>{visible.map(c => <th key={c.key} scope="col">{title(c.key)}</th>)}</tr></thead>
        <tbody>{rows.map(row => <tr key={row.id} className={`${selected.has(row.id) ? "is-selected " : ""}${highlight.ids.has(row.id) ? "is-highlighted" : ""}`} aria-selected={highlight.ids.has(row.id)}
          tabIndex={selectable ? 0 : undefined} aria-label={`Material row ${row.materialName}`}
          onMouseDown={event => { if ((event.shiftKey || event.ctrlKey || event.metaKey) && !isInteractiveTarget(event.target)) event.preventDefault(); }}
          onClick={event => { if (selectable && !active && !lifecycleBusy && !identity && !isInteractiveTarget(event.target)) setHighlight(current => highlightMaterial(current, row.id, rows.map(item => item.id), event)); }}
          onKeyDown={event => { if (selectable && !active && !lifecycleBusy && !identity && event.target === event.currentTarget && event.key === " ") { event.preventDefault(); setHighlight(current => highlightMaterial(current, row.id, rows.map(item => item.id), event)); } }}>
          <td>{selectable && <input type="checkbox" aria-label={`Select ${row.materialName}`} disabled={active || lifecycleBusy} checked={selected.has(row.id)} onChange={e => { const next = new Set(selected); if (e.target.checked) next.add(row.id); else next.delete(row.id); setSelected(next); }} />}</td>
          <td>{expanded ? <MaterialPreviewStrip key={`${row.id}:${previewEpoch}`} material={row} store={store} editable={manager && !row.isArchived && !active && !lifecycleBusy && !identity && !editDialogOpen} onCount={countPreviews} onEdit={(action, filename) => setPreviewEdit({ materials: [{ ...row }], action, filename })} /> : <MaterialThumbnail key={`${row.id}:${previewEpoch}`} material={row} store={store} />}</td>
          {numberColumn && <td>{renderCell("number", row)}</td>}<td className="material-name-column"><div className="material-table-name"><NavigationLink className="table-link" href={row.isArchived ? `/material-archives/${row.id}` : `/materials/${row.id}`} navigate={navigate}>{row.materialName}</NavigationLink>{manager && !row.isArchived && <button className="material-name-edit" aria-label={`Edit name of ${row.materialName}`} title="Edit name" disabled={active || lifecycleBusy || Boolean(identity) || editDialogOpen} onClick={() => setRename({ ...row })}><Icon name="pencil" size={14} /></button>}</div><NavigationLink className="table-identity" href={row.isArchived ? `/material-archives/${row.id}` : `/materials/${row.id}`} navigate={navigate}>{row.technicalIdentity}</NavigationLink></td>
          {visible.map(c => <td key={c.key}>{renderCell(c.key, row)}</td>)}</tr>)}</tbody>
      </table>
    </DatabaseTableViewport>}
    <dialog className="confirm-dialog material-bulk-dialog" ref={dialog} aria-labelledby="material-change-title" onCancel={e => { e.preventDefault(); close(); }}>
      <div className="confirm-dialog__body"><h2 id="material-change-title">Change {jobs.length} material{jobs.length === 1 ? "" : "s"}</h2>
        <p>Selected records are fixed for this operation. Each write checks that the material has not changed.</p>
        {review && <p className="note-text"><strong>{review[0]}</strong> → {review[1]}</p>}
        {jobs[0] && "note" in jobs[0].change && <p>This replaces the existing note on each selected material.</p>}
        {jobs[0] && "main_category_code" in jobs[0].change && <p>Main category changes also rename linked material folders, matching files and metadata. Linked materials must be unpublished and In progress. Checked and automatic checks must be repeated. Ineligible or changed records are skipped and listed below.</p>}
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
    {rename && <MaterialNameDialog material={rename} onClose={() => setRename(null)} onChanged={async () => { store.forget(rename.id, rename.folderPath ?? ""); setPreviewEpoch(value => value + 1); refresh(); return true; }} />}
    {previewEdit && <PreviewEditDialog selection={previewEdit} onClose={() => { setPreviewEdit(null); if (previewsChanged.current) { previewsChanged.current = false; refresh(); } }} onChanged={() => { previewEdit.materials.forEach(row => store.forget(row.id, row.folderPath ?? "")); setPreviewEpoch(value => value + 1); previewsChanged.current = true; }} />}
    {bulkNames && <MaterialBulkNamesDialog materials={bulkNames} client={client} onChanged={() => { bulkNames.forEach(row => store.forget(row.id, row.folderPath ?? "")); namesChanged.current = true; }} onClose={() => { setBulkNames(null); if (namesChanged.current) { namesChanged.current = false; setPreviewEpoch(value => value + 1); refresh(); } }} />}
  </div>;
}
