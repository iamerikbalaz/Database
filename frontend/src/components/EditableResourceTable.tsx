import { useEffect, useRef, useState, type ReactNode } from "react";

import { useNavigationGuard } from "../navigationGuard";
import { ApiError } from "../api/errors";

import { sessionGeneration } from "../auth/sessionTransport";
import { highlightMaterial, isInteractiveTarget, type HighlightState } from "./materialHighlight";
import { DatabaseTableViewport } from "./DatabaseTableViewport";
import { Icon } from "./Icon";



export type ResourceValue = string | boolean | null;

export type ResourceColumn<T> = {

  key: string; label: string; value: (row: T) => ResourceValue;

  options?: { value: string; label: string; disabled?: boolean; currentOnly?: boolean }[];

  type?: "text" | "textarea" | "date" | "boolean";

  editable?: boolean; bulk?: boolean; required?: boolean; render?: (row: T) => ReactNode;

};

type Job<T> = { row: T; field: string; value: ResourceValue; key: string; generation: number; state: "waiting" | "saved" | "failed" | "unknown" | "stopped"; message?: string;

  save: (row: T, field: string, value: ResourceValue, key: string) => Promise<T> };

type Props<T> = { rows: T[]; columns: ResourceColumn<T>[]; label: (row: T) => string;

  save: Job<T>["save"]; canEdit: boolean; refresh: () => void; onBusyChange?: (busy: boolean) => void; storageKey: string; scrollMode?: "page" | "contained" };



function ValueInput({ column, value, set, label, disabled }: { column: { type?: string; options?: { value: string; label: string; disabled?: boolean; currentOnly?: boolean }[] }; value: string; set: (value: string) => void; label: string; disabled: boolean }) {

  if (column.type === "boolean") return <input type="checkbox" aria-label={label} checked={value === "true"} onChange={e => set(String(e.target.checked))} disabled={disabled} />;

  if (column.options) return <select aria-label={label} value={value} onChange={e => set(e.target.value)} disabled={disabled}>{column.options.filter(option => !option.currentOnly || option.value === value).map(option => <option key={option.value} value={option.value} disabled={option.disabled}>{option.label}</option>)}</select>;

  if (column.type === "textarea") return <textarea rows={2} aria-label={label} value={value} onChange={e => set(e.target.value)} disabled={disabled} maxLength={10000} />;

  return <input type={column.type === "date" ? "date" : "text"} aria-label={label} value={value} onChange={e => set(e.target.value)} disabled={disabled} maxLength={2048} />;

}

function cellValue(column: { type?: string }, draft: string): ResourceValue { return column.type === "boolean" ? draft === "true" : draft || null; }

function EditableCell<T>({ row, column, label, disabled, save }: { row: T; column: ResourceColumn<T>; label: string; disabled: boolean; save: (value: ResourceValue) => void }) {

  const original = String(column.value(row) ?? "");

  const [draft, setDraft] = useState(original);

  const immediate = column.options !== undefined || column.type === "boolean";

  return <div className="resource-table-cell"><ValueInput column={column} value={immediate ? original : draft} label={`${column.label} for ${label}`} disabled={disabled}

    set={value => { if (immediate) save(cellValue(column, value)); else setDraft(value); }} />

    {!immediate && draft !== original && <div><button className="button" disabled={disabled} onClick={() => save(cellValue(column, draft))}>Save {column.label.toLowerCase()}</button><button className="button" disabled={disabled} onClick={() => setDraft(original)}>Cancel</button></div>}</div>;

}



/** One immutable request per row. An uncertain write pauses the queue and keeps its key. */

export function EditableResourceTable<T extends { id: string }>({ rows, columns, label, save, canEdit, refresh, onBusyChange, storageKey, scrollMode = "page" }: Props<T>) {

  const [overrides, setOverrides] = useState<Record<string, T>>({});

  const items = rows.map(row => overrides[row.id] ?? row);
  const [highlight, setHighlight] = useState<HighlightState>({ ids: new Set(), anchor: null });

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const visibleKey = rows.map(row => row.id).join(":");
  const [previousVisibleKey, setPreviousVisibleKey] = useState(visibleKey);
  if (previousVisibleKey !== visibleKey) {
    setPreviousVisibleKey(visibleKey);
    const visible = new Set(rows.map(row => row.id));
    setHighlight({ ids: new Set([...highlight.ids].filter(id => visible.has(id))), anchor: highlight.anchor && visible.has(highlight.anchor) ? highlight.anchor : null });
    setSelected(new Set([...selected].filter(id => visible.has(id))));
  }

  const editable = columns.filter(column => column.editable && column.bulk);

  const [field, setField] = useState(editable[0]?.key ?? "");

  const bulkColumn = editable.find(column => column.key === field) ?? editable[0];

  const initialValue = (column?: ResourceColumn<T>) => column?.options?.find(option => !option.disabled)?.value ?? (column?.type === "boolean" ? "true" : "");

  const [value, setValue] = useState(() => initialValue(bulkColumn));

  const [hidden, setHidden] = useState<Set<string>>(() => { try { const saved: unknown = JSON.parse(localStorage.getItem(storageKey) ?? "[]"); return new Set(Array.isArray(saved) ? saved.filter(item => typeof item === "string" && columns.some(column => column.key === item && !column.required)) : []); } catch { return new Set(); } });

  const [jobs, setJobs] = useState<Job<T>[]>([]), jobsRef = useRef<Job<T>[]>([]);

  const [active, setActive] = useState(false), [pending, setPending] = useState(false), [inline, setInline] = useState(false);

  const alive = useRef(true), sending = useRef(false), stop = useRef(false), inlineRef = useRef(false);

  const dialog = useRef<HTMLDialogElement>(null), focus = useRef<HTMLElement | null>(null);
  useNavigationGuard(() => sending.current || jobsRef.current.some(job => job.state === "unknown"));

  useEffect(() => { alive.current = true; return () => { alive.current = false; stop.current = true; }; }, []);

  useEffect(() => { onBusyChange?.(active); return () => onBusyChange?.(false); }, [active, onBusyChange]);

  useEffect(() => { if (!active) return; const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; }; window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn); }, [active]);

  const update = (index: number, patch: Partial<Job<T>>) => { jobsRef.current = jobsRef.current.map((job, i) => i === index ? { ...job, ...patch } : job); if (alive.current) setJobs([...jobsRef.current]); };

  const haltSession = () => {
    jobsRef.current.forEach((job, index) => { if (job.state === "waiting") update(index, { state: "stopped", message: "Session changed. Reload the records before editing again." }); });
  };
  const run = async () => {

    if (sending.current) return;

    sending.current = true; stop.current = false; setPending(true);

    for (let i = 0; i < jobsRef.current.length; i++) {

      const job = jobsRef.current[i];

      if (!["waiting", "unknown"].includes(job.state)) continue;

      if (!alive.current) break;
      if (job.generation !== sessionGeneration()) { haltSession(); break; }

      if (stop.current) { update(i, { state: "stopped", message: "Not attempted" }); continue; }

      try {

        const saved = await job.save(job.row, job.field, job.value, job.key);

        if (!alive.current) break;
        if (job.generation !== sessionGeneration()) { update(i, { state: "saved", message: "Saved before session changed. Reload the record." }); haltSession(); break; }

        setOverrides(previous => ({ ...previous, [saved.id]: saved })); update(i, { state: "saved", message: "Saved" });

      } catch (error) {

        if (!alive.current) break;

        if (job.state !== "unknown" && error instanceof ApiError && error.status >= 400 && error.status < 500) {

          update(i, { state: "failed", message: error.status === 409 ? "Record changed or requirements not met. Reload before editing again." : error.message });

          if (error.status === 401 || error.status === 403) stop.current = true;

        } else { update(i, { state: "unknown", message: job.generation !== sessionGeneration() ? "Outcome unknown and session changed. Check the record history after signing in again; this queue will not continue." : "Outcome unknown. Retry the same request to recover it safely." }); if (job.generation !== sessionGeneration()) haltSession(); break; }

      }

    }

    sending.current = false;

    if (alive.current) { setPending(false); if (inlineRef.current && !jobsRef.current.some(job => job.state === "unknown")) setActive(false); }

  };

  const prepare = (targets: T[], column: ResourceColumn<T>, next: ResourceValue, immediate = false) => {

    if (active || sending.current || !targets.length || column.options && !column.options.some(option => !option.disabled && option.value === String(next ?? ""))) return;

    focus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    jobsRef.current = targets.map(row => ({ row: { ...row }, field: column.key, value: next, key: crypto.randomUUID(), generation: sessionGeneration(), state: "waiting", save }));

    setJobs([...jobsRef.current]); setActive(true); setInline(immediate); inlineRef.current = immediate;

    if (immediate) void run(); else dialog.current?.showModal();

  };

  const close = () => { if (pending || jobs.some(job => job.state === "unknown")) return; dialog.current?.close(); setActive(false); focus.current?.focus(); };

  const results = <ul className="resource-table-results">{jobs.map(job => <li key={job.key}><strong>{label(job.row)}</strong>: {job.message ?? "Waiting"}</li>)}</ul>;

  const refreshRows = () => { setOverrides({}); setSelected(new Set()); setHighlight({ ids: new Set(), anchor: null }); setJobs([]); refresh(); };

  const visible = columns.filter(column => column.required || !hidden.has(column.key));
  const reviewedColumn = columns.find(column => column.key === jobs[0]?.field);
  const reviewedValue = reviewedColumn?.options?.find(option => option.value === String(jobs[0]?.value))?.label
    ?? (typeof jobs[0]?.value === "boolean" ? jobs[0].value ? "Yes" : "No" : String(jobs[0]?.value ?? "Empty"));

  return <div className={`resource-database${scrollMode === "contained" ? " resource-database--contained" : ""}`}>

    <div className="toolbar resource-table-toolbar">

      {canEdit && <button className="button" disabled={active || !items.some(row => highlight.ids.has(row.id))} onClick={() => setSelected(new Set([...selected, ...items.filter(row => highlight.ids.has(row.id)).map(row => row.id)]))}>Select highlighted ({items.filter(row => highlight.ids.has(row.id)).length})</button>}
      {canEdit && <span>{items.filter(row => selected.has(row.id)).length} selected</span>}

      <details className="resource-properties"><summary>Properties</summary><div className="resource-property-options">{columns.map(column => <label key={column.key}><input type="checkbox" checked={column.required || !hidden.has(column.key)} disabled={active || column.required || visible.length === 1 && !hidden.has(column.key)} onChange={() => { const next = new Set(hidden); if (next.has(column.key)) next.delete(column.key); else next.add(column.key); setHidden(next); try { localStorage.setItem(storageKey, JSON.stringify([...next])); } catch { /* In-memory preferences remain available. */ } }} />{column.label}</label>)}</div></details>

      <button className="button resource-table-refresh" aria-label="Refresh" title="Refresh" disabled={active} onClick={refreshRows}><Icon name="refresh" size={20} /></button>
    </div>
    {canEdit && bulkColumn && items.some(row => selected.has(row.id)) && <fieldset className="material-bulk-bar resource-bulk-bar" disabled={active}><legend>Apply to {items.filter(row => selected.has(row.id)).length} selected records</legend>
      <label>Property<select aria-label="Bulk property" value={bulkColumn.key} onChange={e => { setField(e.target.value); setValue(initialValue(editable.find(column => column.key === e.target.value))); }}>{editable.map(column => <option key={column.key} value={column.key}>{column.label}</option>)}</select></label>
      <label>New value<ValueInput column={bulkColumn} label="Bulk value" value={value} set={setValue} disabled={active} /></label>
      <button className="button button--primary" disabled={!items.some(row => selected.has(row.id)) || Boolean(bulkColumn.options && !bulkColumn.options.some(option => !option.disabled && option.value === value))} onClick={() => prepare(items.filter(row => selected.has(row.id)), bulkColumn, cellValue(bulkColumn, value))}>Review bulk change</button>
    </fieldset>}

    {inline && jobs.length > 0 && <div role="status">{pending ? "Saving…" : jobs.every(job => job.state === "saved") ? "Saved. Refresh to reapply filters." : "Review the result."}{!pending && results}{jobs.some(job => job.state === "unknown") && <button className="button" disabled={pending || jobs.some(job => job.state === "unknown" && job.generation !== sessionGeneration())} onClick={() => void run()}>Retry same request</button>}</div>}

    <DatabaseTableViewport scrollMode={scrollMode} className="table-card resource-table-scroll" label="Database results"><table><thead><tr>{canEdit && <th><input aria-label="Select all filtered rows" type="checkbox" disabled={active || !items.length} ref={element => { if (element) element.indeterminate = items.some(row => selected.has(row.id)) && !items.every(row => selected.has(row.id)); }} checked={items.length > 0 && items.every(row => selected.has(row.id))} onChange={e => setSelected(e.target.checked ? new Set(items.map(row => row.id)) : new Set())} /></th>}{visible.map(column => <th key={column.key}>{column.label}</th>)}</tr></thead>

      <tbody>{items.map(row => <tr key={row.id} aria-label={`Record row ${label(row)}`} aria-selected={highlight.ids.has(row.id)} tabIndex={canEdit ? 0 : undefined} className={`${selected.has(row.id) ? "is-selected " : ""}${highlight.ids.has(row.id) ? "is-highlighted" : ""}`}
        onMouseDown={event => { if ((event.shiftKey || event.ctrlKey || event.metaKey) && !isInteractiveTarget(event.target)) event.preventDefault(); }}
        onClick={event => { if (canEdit && !active && !isInteractiveTarget(event.target)) setHighlight(current => highlightMaterial(current, row.id, items.map(item => item.id), event)); }}
        onKeyDown={event => { if (canEdit && !active && event.target === event.currentTarget && event.key === " ") { event.preventDefault(); setHighlight(current => highlightMaterial(current, row.id, items.map(item => item.id), event)); } }}>{canEdit && <td><input aria-label={`Select ${label(row)}`} type="checkbox" disabled={active} checked={selected.has(row.id)} onChange={e => { const next = new Set(selected); if (e.target.checked) next.add(row.id); else next.delete(row.id); setSelected(next); }} /></td>}{visible.map(column => <td key={column.key}>{column.render?.(row)}{canEdit && column.editable ? <EditableCell key={`${row.id}:${column.key}:${String(column.value(row))}`} row={row} column={column} label={label(row)} disabled={active} save={next => prepare([row], column, next, true)} /> : !column.render && (column.type === "boolean" ? column.value(row) ? "Yes" : "No" : (column.options?.find(option => option.value === String(column.value(row)))?.label ?? column.value(row)) || "—")}</td>)}</tr>)}</tbody></table></DatabaseTableViewport>

    <dialog ref={dialog} className="resource-bulk-dialog" aria-labelledby={`${storageKey}-bulk-title`} onCancel={event => { event.preventDefault(); close(); }}><h2 id={`${storageKey}-bulk-title`}>Review changes to {jobs.length} records</h2><p>{reviewedColumn?.label}: {reviewedValue}</p><p>The current filtered or selected rows and their versions are fixed for this operation. Results are recorded separately for every row.</p>{results}

      {!pending && jobs.some(job => job.state === "waiting" || job.state === "unknown") && <button className="button button--primary" disabled={jobs.some(job => job.state === "unknown" && job.generation !== sessionGeneration())} onClick={() => void run()}>{jobs.some(job => job.state === "unknown") ? "Retry same request and continue" : "Confirm changes"}</button>}

      {pending && <button className="button" onClick={() => { stop.current = true; }}>Stop after current record</button>}<button className="button" disabled={pending || jobs.some(job => job.state === "unknown")} onClick={close}>Close</button>

    </dialog>

  </div>;

}
