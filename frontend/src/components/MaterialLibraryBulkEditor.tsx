import { useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import type { Material } from "../api/materialDto";
import { applyLibraryChange, libraryChange, libraryFieldLabels, prepareLibraryChange, type LibraryEditorField, type LibraryField, type LibraryPacket } from "../api/materialLibraryClient";
import { useSession } from "../auth/context";
import { sessionGeneration } from "../auth/sessionTransport";
import { useNavigationGuard } from "../navigationGuard";
import { MaterialColorSelect } from "./MaterialColorSelect";
import { ColoredSelect } from "./ColoredSelect";
import { materialStatusColors, materialCheckedColors } from "../data/choiceColors";
import "./MaterialLibraryBulkEditor.css";

type Job = { material: Material; packet?: LibraryPacket; before?: string; after?: string; operationId?: string; status: "loading" | "ready" | "saved" | "failed" | "unknown"; message?: string };
export interface MaterialLibraryBulkEditorProps {
  materials: Material[]; client: ApiClient; onChanged: () => void | Promise<void>;
  onBusyChange?: (busy: boolean) => void; disabled?: boolean; field?: LibraryField; includeWorkflow?: boolean;
}

export function MaterialLibraryBulkEditor({ materials, client, onChanged, onBusyChange, disabled, field: providedField, includeWorkflow = false }: MaterialLibraryBulkEditorProps) {
  const role = useSession()?.session.user.role;
  const allowed = role === "ADMIN" || role === "PRODUCTION_LEAD" || role === "PROCESSOR";
  const [localField, setLocalField] = useState<LibraryEditorField>("color"), [value, setValue] = useState(""), [height, setHeight] = useState("");
  const field = providedField ?? localField;
  const [jobs, setJobs] = useState<Job[] | null>(null), jobsRef = useRef<Job[] | null>(null);
  const [pending, setPending] = useState(false), [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null), alive = useRef(true), sending = useRef(false);
  const generation = useRef(sessionGeneration()), changed = useRef(false), focus = useRef<HTMLElement | null>(null);
  const dialogOpen = jobs !== null;
  const current = () => alive.current && generation.current === sessionGeneration();
  const update = (index: number, patch: Partial<Job>) => {
    jobsRef.current = jobsRef.current!.map((job, i) => i === index ? { ...job, ...patch } : job);
    if (current()) setJobs(jobsRef.current);
  };
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { onBusyChange?.(dialogOpen); return () => onBusyChange?.(false); }, [dialogOpen, onBusyChange]);
  useEffect(() => { if (jobs && !dialog.current?.open) dialog.current?.showModal(); }, [jobs]);
  useNavigationGuard(() => sending.current || Boolean(jobsRef.current?.some(job => job.status === "unknown")));
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (sending.current || jobsRef.current?.some(job => job.status === "unknown")) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, []);
  const prepare = async () => {
    if (!allowed || disabled || sending.current || jobsRef.current || !materials.length || materials.length > 100) return;
    setError("");
    let change;
    try { change = libraryChange(field, value, height); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Enter a valid value."); return; }
    const frozen = materials.map(material => ({ ...material }));
    if (new Set(frozen.map(material => material.id)).size !== frozen.length) { setError("Select each material only once."); return; }
    generation.current = sessionGeneration(); changed.current = false;
    focus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    sending.current = true; setPending(true);
    jobsRef.current = frozen.map(material => ({ material, status: "loading" })); setJobs(jobsRef.current);
    for (let i = 0; i < frozen.length; i++) {
      if (!current()) break;
      try {
        const prepared = await prepareLibraryChange(frozen[i], change, client);
        if (!current()) break;
        update(i, { ...prepared, status: "ready" });
      } catch (cause) {
        if (!current()) break;
        update(i, { status: "failed", message: cause instanceof ApiError ? cause.message : "Current library data could not be loaded. Nothing was changed." });
      }
    }
    sending.current = false; if (current()) setPending(false);
  };
  const run = async () => {
    if (sending.current || !jobsRef.current || !current()) return;
    sending.current = true; setPending(true);
    for (let i = 0; i < jobsRef.current.length; i++) {
      const job = jobsRef.current[i];
      if (!current()) break;
      if (!job.packet || !["ready", "unknown"].includes(job.status)) continue;
      try {
        const result = await applyLibraryChange(job.material.id, job.packet, job.operationId);
        if (!current()) break;
        if (result.status === "RUNNING") {
          update(i, { status: "unknown", operationId: result.operationId, message: "Source save is pending verification. Recover this same operation before continuing." }); break;
        }
        update(i, { status: result.status === "COMPLETED" ? "saved" : "failed", message: result.status === "COMPLETED" ? "Saved" : "Source or material changed. No library change was applied." });
        if (result.status === "COMPLETED") changed.current = true;
      } catch (cause) {
        if (!current()) break;
        if (job.status !== "unknown" && !job.operationId && cause instanceof ApiError && (cause.status >= 400 && cause.status < 500 || ["SOURCE_MUTATIONS_DISABLED", "SOURCE_METADATA_UNAVAILABLE", "TABLE_PREFLIGHT_UNAVAILABLE"].includes(cause.code ?? ""))) {
          update(i, { status: "failed", message: cause.code === "CHECKED_REQUIRES_DONE" ? "Set Status to Done before setting Checked to OK. Correction returns Status to In progress." : cause.code === "TABLE_PREFLIGHT_UNAVAILABLE" ? "Folder checking service unavailable. No change was made; remaining materials were stopped." : cause.message });
          if (cause.status === 401 || cause.status === 403 || cause.code === "TABLE_PREFLIGHT_UNAVAILABLE") break;
        } else {
          update(i, { status: "unknown", message: "Outcome unknown. Retry the exact request before continuing." }); break;
        }
      }
    }
    sending.current = false; if (current()) setPending(false);
  };
  const close = () => {
    if (sending.current || jobsRef.current?.some(job => job.status === "unknown")) return;
    dialog.current?.close(); jobsRef.current = null; setJobs(null); focus.current?.focus();
    if (changed.current) { changed.current = false; void onChanged(); }
  };
  const blocked = disabled || !allowed || jobs !== null;
  const unknown = jobs?.some(job => job.status === "unknown"), ready = jobs?.some(job => job.status === "ready");
  return <>
    {!providedField && <label>Property<select value={field} disabled={blocked} onChange={event => { setLocalField(event.target.value as LibraryEditorField); setValue(event.target.value === "workflow_status" ? "DONE" : event.target.value === "checked_status" ? "OK" : ""); setHeight(""); setError(""); }}>
      {Object.entries(libraryFieldLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      {includeWorkflow && <><option value="workflow_status">Status</option>{(role === "ADMIN" || role === "PRODUCTION_LEAD") && <option value="checked_status">Checked</option>}</>}
    </select></label>}
    <div className="material-library-bulk-value"><span>New value</span>
      {field === "workflow_status" || field === "checked_status" ? <ColoredSelect aria-label="New value" value={value} disabled={blocked} onChange={event => setValue(event.target.value)}
        colors={field === "workflow_status" ? materialStatusColors : materialCheckedColors} options={field === "workflow_status" ? [{ value: "DONE", label: "Done" }, { value: "IN_PROGRESS", label: "In progress" }] : ["no", "OK", "Correction"].map(status => ({ value: status, label: status }))} /> :
      field === "color" ? <MaterialColorSelect value={value} onChange={setValue} disabled={blocked} /> : field === "sample_size" ?
        <div className="material-library-bulk-size"><input type="number" aria-label="Sample width (cm)" min="0.1" max="99999999.9" step="0.1" value={value} disabled={blocked} onChange={event => setValue(event.target.value)} /><span>×</span><input type="number" aria-label="Sample height (cm)" min="0.1" max="99999999.9" step="0.1" value={height} disabled={blocked} onChange={event => setHeight(event.target.value)} /></div> :
        <input aria-label={field === "tags" ? "Tags to append" : "Credits"} type={field === "credits" ? "number" : "text"} min={field === "credits" ? "0" : undefined} max={field === "credits" ? "2147483647" : undefined} step={field === "credits" ? "1" : undefined} value={value} placeholder={field === "tags" ? "matte, stone" : "Not set"} disabled={blocked} onChange={event => setValue(event.target.value)} />}
    </div>
    <button type="button" className="button button--primary" disabled={blocked || !materials.length || materials.length > 100} onClick={() => void prepare()}>Review bulk change</button>
    {error && <p role="alert" className="field-error material-library-bulk-error">{error}</p>}
    <dialog className="material-library-bulk-dialog" ref={dialog} aria-label="Review library changes" onCancel={event => { event.preventDefault(); close(); }}>
      <h2>Review library changes</h2><p>{jobs?.length ?? 0} selected materials. Existing values are shown below. Only the selected property changes.</p>
      {(field === "color" || field === "sample_size") && <p>Color and sample size are saved to the linked metadata.json. Other metadata values are preserved.</p>}
      {field === "tags" && <p>Tags are added to existing tags. Duplicate tags are ignored.</p>}
      {field === "workflow_status" && <p>Done checks the linked folder and resets Checked to no.</p>}
      {field === "checked_status" && <p>Checked OK requires Done. Correction returns Status to In progress.</p>}
      <p>Changes reset Checked and Automatic file check when required.</p>
      <p role="status">{jobs?.filter(job => job.status === "saved").length ?? 0} saved · {jobs?.filter(job => job.status === "failed").length ?? 0} rejected{pending ? " · Working…" : ""}</p>
      <ul className="material-library-bulk-report">{jobs?.map(job => <li key={job.material.id}><strong>{job.material.materialName}</strong>
        {job.before !== undefined && <span>{job.before} → <strong>{job.after}</strong></span>}
        <span className={job.status === "failed" || job.status === "unknown" ? "field-error" : ""}>{job.message ?? (job.status === "loading" ? "Loading…" : "Ready")}</span>
      </li>)}</ul>
      <div className="form-actions"><button className="button" type="button" disabled={pending || unknown} onClick={close}>Close</button>
        {(ready || unknown) && <button className="button button--primary" type="button" disabled={pending} onClick={() => void run()}>{unknown ? "Retry same request and continue" : "Apply library changes"}</button>}
      </div>
    </dialog>
  </>;
}
