import { useEffect, useMemo, useRef, useState } from "react";
import type { Material } from "../api/materialDto";
import { previewEditClient, type PreviewEditConfirmation, type PreviewEditPlan, type PreviewEditRequest, type PreviewEditResult } from "../api/previewEditClient";
import { ApiError } from "../api/errors";
import { sessionGeneration } from "../auth/sessionTransport";
import { useNavigationGuard } from "../navigationGuard";
import "./MaterialPreviews.css";

export type PreviewEditSelection = { materials: Material[]; action: "RENAME" | "DELETE" | "BULK"; filename?: string };
export function PreviewEditDialog({ selection, onClose, onChanged }: {
  selection: PreviewEditSelection; onClose: () => void; onChanged: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null), sending = useRef(false), exact = useRef<PreviewEditConfirmation | null>(null);
  const [generation] = useState(sessionGeneration), [name, setName] = useState(selection.filename ?? "");
  const [find, setFind] = useState(""), [replace, setReplace] = useState(""), [remove, setRemove] = useState("");
  const [plan, setPlan] = useState<{ input: string; value: PreviewEditPlan } | null>(null);
  const [planning, setPlanning] = useState(false), [pending, setPending] = useState(false), [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState(""), [recovery, setRecovery] = useState<PreviewEditResult | null>(null), [result, setResult] = useState<PreviewEditResult | null>(null);
  const [historyReady, setHistoryReady] = useState(false), [historyAttempt, setHistoryAttempt] = useState(0);
  const [planAttempt, setPlanAttempt] = useState(0);
  const planQueue = useRef<Promise<void>>(Promise.resolve());
  const request = useMemo<PreviewEditRequest>(() => ({
    materials: selection.materials.map(item => ({ id: item.id, expected_updated_at: item.updatedAt })), action: selection.action,
    ...(selection.action === "BULK" ? { ...(find ? { find, replace } : {}), ...(remove ? { delete_containing: remove } : {}), case_sensitive: true } : { filename: selection.filename, ...(selection.action === "RENAME" ? { new_name: name } : {}) }),
  }), [selection, name, find, replace, remove]);
  const inputKey = JSON.stringify(request);
  const valid = selection.action === "DELETE" || (selection.action === "RENAME" ? name.trim() !== selection.filename && !!name.trim() : !!find || !!remove);
  const currentPlan = plan?.input === inputKey ? plan.value : null;
  const locked = pending || uncertain || recovery !== null || result !== null;
  useNavigationGuard(() => sending.current || exact.current !== null || recovery !== null);
  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.showModal(); return () => trigger?.focus();
  }, []);
  useEffect(() => {
    let live = true;
    void previewEditClient.pending(selection.materials.map(item => item.id)).then(items => {
      if (!live || generation !== sessionGeneration()) return;
      const ids = new Set(selection.materials.map(item => item.id));
      setRecovery(items.find(item => ["RUNNING", "RECOVERY_REQUIRED"].includes(item.status) && item.items.some(row => ids.has(row.material_id))) ?? null);
      setHistoryReady(true);
    }, () => { if (live) setError("Pending preview operations could not be checked. Reload before editing source files."); });
    return () => { live = false; };
  }, [selection, generation, historyAttempt]);
  useEffect(() => {
    if (!valid || !historyReady || locked || generation !== sessionGeneration()) return;
    let live = true;
    const timer = window.setTimeout(() => {
      // A bulk plan can inspect many files. Keep at most one request in flight;
      // superseded queued inputs are skipped instead of rereading the disk.
      planQueue.current = planQueue.current.then(async () => {
        if (!live || generation !== sessionGeneration()) return;
        setPlanning(true); setError("");
        try { const value = await previewEditClient.plan(request); if (live && generation === sessionGeneration()) setPlan({ input: inputKey, value }); }
        catch (cause) { if (live) setError(cause instanceof ApiError ? cause.message : "Preview filenames could not be checked. Retry the preview review."); }
        finally { if (live) setPlanning(false); }
      });
    }, 300);
    return () => { live = false; window.clearTimeout(timer); };
  }, [request, inputKey, valid, historyReady, locked, generation, planAttempt]);
  const save = async () => {
    if (sending.current || generation !== sessionGeneration()) return;
    if (!exact.current && !recovery) {
      if (!currentPlan?.can_apply || !(currentPlan.total_renames + currentPlan.total_deletes)) return;
      exact.current = { ...request, idempotency_key: crypto.randomUUID(), expected_proposal_hash: currentPlan.proposal_hash, confirmed: true };
    }
    sending.current = true; setPending(true); setError("");
    const wasUncertain = uncertain || recovery !== null;
    try {
      const value = recovery ? await previewEditClient.resume(recovery.id) : await previewEditClient.apply(exact.current!);
      if (generation !== sessionGeneration()) { setUncertain(true); setError("The session changed. Sign in as the original author to recover this operation."); return; }
      if (["RUNNING", "RECOVERY_REQUIRED"].includes(value.status)) { setRecovery(value); setUncertain(true); setError("Some file changes need recovery. Continue the recorded operation."); return; }
      exact.current = null; setRecovery(null); setUncertain(false); setResult(value); onChanged();
    } catch (cause) {
      // Apply persists a receipt before touching files. A generic 403/409 can
      // occur during recovery, so only proven pre-write failures are editable.
      const preflightCodes = ["PREVIEW_PLAN_CHANGED", "PREVIEW_PLAN_BLOCKED", "PREVIEW_REQUEST_KEY_REUSED", "PREVIEW_OPERATION_CONFLICT", "SOURCE_MUTATIONS_DISABLED", "LOCAL_DESKTOP_UNAVAILABLE", "LOCAL_DESKTOP_ONLY"];
      if (!wasUncertain && cause instanceof ApiError && (cause.status === 422 || preflightCodes.includes(cause.code ?? ""))) {
        exact.current = null; setUncertain(false); setPlan(null); setError(cause.message);
      } else { setUncertain(true); setError("The result is unknown. Retry the same operation to recover it safely."); }
    } finally { sending.current = false; setPending(false); }
  };
  const close = () => { if (!sending.current && !uncertain && !recovery) onClose(); };
  const title = selection.action === "BULK" ? "Edit previews" : selection.action === "DELETE" ? "Delete preview" : "Rename preview";
  return <dialog ref={dialog} className="confirm-dialog preview-edit-dialog" aria-label={title} onCancel={event => { event.preventDefault(); close(); }}>
    <h2>{title}</h2>
    <p>{selection.materials.length} material{selection.materials.length === 1 ? "" : "s"} selected. Changes apply to the source PNG files in PREVIEW.</p>
    <fieldset disabled={locked}><legend className="sr-only">Preview filenames</legend>
      {selection.action === "BULK" ? <>
        <div className="preview-edit-replace"><label>Replace text<input value={find} maxLength={200} onChange={event => setFind(event.target.value)} /></label>
          <label>With<input value={replace} maxLength={200} onChange={event => setReplace(event.target.value)} /></label></div>
        <label>Delete previews containing text<input value={remove} maxLength={200} onChange={event => setRemove(event.target.value)} /></label>
        <small>Case-sensitive; matches filenames without .png. Delete matches use the original name and take precedence over replacement. Leave a field empty to skip that action.</small>
      </> : <><p className="preview-edit-filename">{selection.filename}</p>{selection.action === "RENAME" && <label>New filename<input value={name} maxLength={255} onChange={event => setName(event.target.value)} /></label>}</>}
    </fieldset>
    {error && <p className="form-error" role="alert">{error}</p>}
    {!historyReady && <button className="button" onClick={() => { setError(""); setHistoryAttempt(value => value + 1); }}>Reload pending operations</button>}
    {planning && valid && !locked && <p role="status">Checking source filenames…</p>}
    {currentPlan && !result && <section aria-label="Preview changes">
      <p role="status"><strong>{currentPlan.total_renames}</strong> previews to rename · <strong>{currentPlan.total_deletes}</strong> previews to delete</p>
      <div className="preview-edit-report">{currentPlan.items.filter(item => item.changes.length || item.issues.length).map(item => <div key={item.material_id}>
        <strong>{item.identity}</strong>{item.issues.map((issue, i) => <p className="field-error" key={i}>{issue.message}</p>)}
        <ul>{item.changes.map(change => <li key={change.from}>{change.from} → {change.to ?? "DELETE"}</li>)}</ul>
      </div>)}</div>
      <p className="muted">Changed materials become unpublished; Checked and Automatic file check reset for a new review.</p>
    </section>}
    {result && <section aria-label="Preview edit result"><p role="status">{result.status === "COMPLETED" ? "Preview changes completed." : "Review the results. Some materials could not be changed."}</p>
      <ul>{result.items.map(item => <li key={item.material_id}>{selection.materials.find(material => material.id === item.material_id)?.materialName ?? item.material_id}: {item.renamed} renamed, {item.deleted} deleted{item.error_code ? ` · ${item.error_code}` : ""}</li>)}</ul></section>}
    {recovery && <p role="alert">A previous preview operation is unfinished. Recover it before editing these materials.</p>}
    <div className="form-actions"><button className="button" disabled={pending || uncertain || recovery !== null} onClick={close}>{result ? "Close" : "Cancel"}</button>
      {!result && <button className="button button--primary" disabled={pending || generation !== sessionGeneration() || (!uncertain && !recovery && (!currentPlan?.can_apply || !(currentPlan.total_deletes + currentPlan.total_renames)))} onClick={() => void save()}>
        {pending ? "Applying…" : uncertain || recovery ? "Recover preview changes" : currentPlan?.total_deletes ? "Confirm rename / delete previews" : "Confirm rename previews"}</button>}
      {!locked && historyReady && valid && !currentPlan && !planning && <button className="button" onClick={() => setPlanAttempt(value => value + 1)}>Retry preview review</button>}
    </div>
  </dialog>;
}
