import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/errors";
import { materialDeletionClient, type MaterialDeletionConfirmation, type MaterialDeletionMode, type MaterialDeletionPlan, type MaterialDeletionResult } from "../api/materialDeletionClient";
import type { Material } from "../api/materialDto";
import { useSession } from "../auth/context";
import { sessionGeneration } from "../auth/sessionTransport";
import { useNavigationGuard } from "../navigationGuard";
import "./MaterialDeleteDialog.css";

const unfinished = (result: MaterialDeletionResult) => result.status === "RUNNING" || result.status === "RECOVERY_REQUIRED";
const modeLabel = (mode: MaterialDeletionMode) => mode === "RECORD_ONLY" ? "Records only" : "Records and source folders";
const prewriteCodes = new Set(["MATERIAL_DELETE_PLAN_CHANGED", "MATERIAL_DELETE_PLAN_BLOCKED", "MATERIAL_DELETE_REQUEST_KEY_REUSED", "MATERIAL_DELETE_OPERATION_CONFLICT"]);
const warningLabels: Record<string, string> = {
  RECORDS_REMOVED_FROM_ALL_VIEWS: "Deleted records disappear from Materials and Archived materials.",
  AUDIT_RETAINED: "Deletion history is retained.",
  PUBLISHED_EXTERNAL_FILES_UNCHANGED: "Published library copies and files already uploaded to Google Cloud Storage stay unchanged.",
  DATA_RECOVERY_QUARANTINE_RETAINED: "Source folders move into protected recovery quarantine on the same disk. Files remain available for recovery.",
  SOURCE_FILES_UNCHANGED: "Source folders and files stay in their current location.",
};
const issueLabels: Record<string, string> = {
  MATERIAL_DELETE_CHANGED: "This material changed. Refresh the selection before deleting it.",
  MATERIAL_DELETE_PLAN_CHANGED: "The selected materials or source folders changed.",
  MATERIAL_DELETE_PLAN_BLOCKED: "One or more selected materials cannot be deleted.",
  MATERIAL_DELETE_REQUEST_KEY_REUSED: "This deletion request no longer matches its recorded details.",
  MATERIAL_DELETE_OPERATION_CONFLICT: "Another operation is already using a selected material.",
  MATERIAL_DELETE_FOLDER_REQUIRED: "No source folder is linked. Choose Records only to delete this record.",
  MATERIAL_DELETE_FOLDER_UNSAFE: "The linked folder must belong to this material inside its customer folder.",
  MATERIAL_DELETE_FOLDER_SHARED: "Another material uses this folder or a folder inside it. Resolve the folder links first.",
  MATERIAL_DELETE_SOURCE_UNAVAILABLE: "The source folder could not be checked. Check the drive connection, open files and access permissions.",
  MATERIAL_DELETE_SOURCE_CHANGED: "The source folder changed during deletion. Check its contents before trying again.",
  MATERIAL_DELETE_SOURCE_MISSING: "The source folder is no longer available. Check the drive connection and folder location.",
  MATERIAL_DELETE_QUARANTINE_CHANGED: "The recovery folder changed. An administrator must check it before recovery can continue.",
  MATERIAL_DELETE_OWNERSHIP_CHANGED: "Another operation took control of this material. Check its operation history.",
  MATERIAL_DELETE_IO_INTERRUPTED: "The folder move was interrupted. Recover the recorded deletion to continue.",
  MATERIAL_DELETE_OPERATION_NOT_FOUND: "The recorded deletion could not be found. Refresh the material list.",
};
const statusLabels: Record<MaterialDeletionResult["status"], string> = {
  RUNNING: "In progress", RECOVERY_REQUIRED: "Recovery required", COMPLETED: "Completed", PARTIAL: "Partly completed", REJECTED: "Not deleted",
};
function issueLabel(code: string, message?: string) {
  return issueLabels[code] ?? (message && !/^[A-Z][A-Z0-9_]+$/.test(message) ? message : "This material could not be deleted. Check its details and operation history.");
}
function sameIds(expected: string[], actual: string[]) {
  return expected.length === actual.length && new Set(actual).size === actual.length && expected.every(id => actual.includes(id));
}
function validResult(result: MaterialDeletionResult) {
  const complete = result.items.filter(item => item.status === "COMPLETED").length;
  const pending = result.items.some(item => item.status === "RUNNING" || item.status === "RECOVERY_REQUIRED");
  return result.items.length > 0 && result.deleted_count === complete && new Set(result.items.map(item => item.material_id)).size === result.items.length &&
    (unfinished(result) ? pending : !pending && (result.status === "COMPLETED" ? complete === result.items.length : result.status === "REJECTED" ? complete === 0 : complete > 0 && complete < result.items.length));
}

export function MaterialDeleteDialog({ materials, onClose, onFinished }: {
  materials: Material[]; onClose: () => void; onFinished: () => void;
}) {
  const admin = useSession()?.session.user.role === "ADMIN";
  const [selection] = useState(() => materials.map(item => ({ ...item })));
  const [generation] = useState(sessionGeneration);
  const validSelection = selection.length > 0 && selection.length <= 100 && new Set(selection.map(item => item.id)).size === selection.length;
  const [mode, setMode] = useState<MaterialDeletionMode>("RECORD_ONLY");
  const [plan, setPlan] = useState<MaterialDeletionPlan | null>(null);
  const [operations, setOperations] = useState<MaterialDeletionResult[]>([]);
  const operationsRef = useRef<MaterialDeletionResult[]>([]);
  const [ready, setReady] = useState(false), [loadAttempt, setLoadAttempt] = useState(0);
  const [planLoading, setPlanLoading] = useState(false), [planAttempt, setPlanAttempt] = useState(0), [planStopped, setPlanStopped] = useState(false);
  const [busy, setBusy] = useState(false), [started, setStarted] = useState(false), [unknown, setUnknown] = useState(false);
  const [recoveryAcknowledged, setRecoveryAcknowledged] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null), mounted = useRef(false), sending = useRef(false), finished = useRef(false);
  // Retain this exact packet until a verified receipt: even a later 403 does not prove that the first write failed.
  const exact = useRef<MaterialDeletionConfirmation | null>(null);
  const resuming = useRef<MaterialDeletionResult | null>(null);
  const sameSession = () => generation === sessionGeneration();
  const recoveries = operations.filter(unfinished);
  const locked = busy || started || unknown || recoveries.length > 0;
  const planReady = Boolean(plan?.can_apply && plan.mode === mode && !plan.items.some(item => item.issues.length > 0));
  const updateOperations = (values: MaterialDeletionResult[]) => { operationsRef.current = values; setOperations(values); };
  useNavigationGuard(() => sending.current || exact.current !== null || resuming.current !== null || operationsRef.current.some(unfinished));
  useEffect(() => {
    mounted.current = true;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.showModal();
    return () => { mounted.current = false; trigger?.focus(); };
  }, []);
  useEffect(() => {
    if (!admin || !validSelection) return;
    let live = true;
    void materialDeletionClient.pending(selection.map(item => item.id)).then(values => {
      if (!live || generation !== sessionGeneration()) return;
      if (new Set(values.map(item => item.id)).size !== values.length || values.some(item => !unfinished(item) || !validResult(item) || !item.items.some(row => selection.some(selected => selected.id === row.material_id)))) throw new Error("Invalid pending operations");
      operationsRef.current = values; setOperations(values); setReady(true); setError(""); setPlanLoading(values.length === 0);
    }).catch(() => { if (live) setError("Recorded deletions could not be checked. Reload them before continuing."); });
    return () => { live = false; };
  }, [admin, validSelection, selection, generation, loadAttempt]);

  useEffect(() => {
    if (!admin || generation !== sessionGeneration() || !ready || !validSelection || operations.length > 0 || started || planStopped) return;
    let live = true;
    void materialDeletionClient.plan({ mode, materials: selection.map(item => ({ id: item.id, expected_updated_at: item.updatedAt })) }).then(value => {
      if (!live || generation !== sessionGeneration()) return;
      if (value.mode !== mode || value.total !== selection.length || !sameIds(selection.map(item => item.id), value.items.map(item => item.material_id)) || value.items.some(item => selection.find(row => row.id === item.material_id)?.materialName !== item.material_name)) throw new Error("The returned plan does not match the selected materials. Close and refresh the list.");
      setPlan(value);
    }).catch(cause => {
      if (live && generation === sessionGeneration()) setError(cause instanceof ApiError ? issueLabel(cause.code ?? "") : cause instanceof Error ? cause.message : "The selected materials could not be checked. Try again.");
    }).finally(() => { if (live) setPlanLoading(false); });
    // Read-only requests may finish after a mode change or closing. Their results must never replace the current review.
    return () => { live = false; };
  }, [admin, generation, ready, validSelection, selection, mode, operations.length, started, planStopped, planAttempt]);
  const changeMode = (next: MaterialDeletionMode) => {
    setMode(next); setPlan(null); setError(""); setPlanLoading(true);
  };
  const save = async (operation?: MaterialDeletionResult) => {
    if (!admin || !sameSession() || sending.current) return;
    const recovery = operation ?? resuming.current;
    const retry = exact.current !== null || resuming.current !== null;
    if (recovery && !retry && !recoveryAcknowledged) return;
    if (!recovery && !exact.current) {
      if (!plan || !planReady || planLoading || planStopped || locked || !ready) return;
      exact.current = { mode: plan.mode, materials: selection.map(item => ({ id: item.id, expected_updated_at: item.updatedAt })), expected_proposal_hash: plan.proposal_hash, idempotency_key: crypto.randomUUID(), confirmed: true };
    }
    if (recovery) resuming.current = recovery;
    sending.current = true; setBusy(true); setStarted(true); setError("");
    try {
      const expected = recovery ? recovery.items.map(item => item.material_id) : exact.current!.materials.map(item => item.id);
      const expectedMode = recovery?.mode ?? exact.current!.mode;
      const result = recovery ? await materialDeletionClient.resume(recovery.id) : await materialDeletionClient.apply(exact.current!);
      if (!sameSession()) throw new Error("The session changed after submission.");
      if (!validResult(result) || result.mode !== expectedMode || !sameIds(expected, result.items.map(item => item.material_id)) || (recovery && result.id !== recovery.id)) throw new Error("The receipt does not match the submitted deletion.");
      exact.current = null; resuming.current = null; finished.current = true;
      if (!mounted.current) return;
      updateOperations([...operationsRef.current.filter(item => item.id !== result.id), result]); setUnknown(false);
      if (unfinished(result)) setError("The recorded deletion needs recovery. Review its full material list below, then resume the same operation.");
    } catch (cause) {
      if (!mounted.current) return;
      if (!retry && !recovery && cause instanceof ApiError && cause.code && prewriteCodes.has(cause.code)) {
        exact.current = null; setPlan(null); setPlanStopped(true); setStarted(false); setUnknown(false);
        setError("The deletion was rejected before changes. " + issueLabel(cause.code) + " Close and refresh the selection before trying again.");
      } else {
        setUnknown(true);
        setError("The deletion result is unknown. Keep this dialog open and recover the same request; no new deletion will be submitted.");
      }
    } finally { sending.current = false; if (mounted.current) setBusy(false); }
  };
  const close = () => {
    if (sending.current || exact.current || resuming.current || operationsRef.current.some(unfinished)) return;
    if (finished.current) { finished.current = false; onFinished(); }
    onClose();
  };
  return <dialog ref={dialog} className="material-delete-dialog" aria-label="Delete selected materials" onCancel={event => { event.preventDefault(); close(); }}>
    <header className="material-delete-header"><h2>Delete materials</h2>
      <p>{selection.length} explicitly selected material records.</p>
    </header>
    <div className="material-delete-body">
    <p className="material-delete-intro">Published library copies and files already uploaded to Google Cloud Storage stay unchanged.</p>
    {!admin && <p role="alert">Only administrators can delete materials.</p>}
    {!validSelection && <p role="alert">Select between 1 and 100 distinct materials.</p>}
    {operations.length === 0 && <fieldset disabled={!admin || !ready || !validSelection || locked || planStopped}><legend>What should be removed?</legend>
      <label><input type="radio" name="material-deletion-mode" checked={mode === "RECORD_ONLY"} onChange={() => changeMode("RECORD_ONLY")} /><span>Records only<small>Keep all source folders and files in their current location.</small></span></label>
      <label><input type="radio" name="material-deletion-mode" checked={mode === "RECORD_AND_FILES"} onChange={() => changeMode("RECORD_AND_FILES")} /><span>Records and source folders<small>Move each entire source folder out of the material library into protected recovery quarantine on the same disk. Files are retained for recovery, not permanently erased.</small></span></label>
    </fieldset>}
    {error && <p role="alert" className="field-error">{error}</p>}
    {!ready && validSelection && admin && (error ? <button className="button" disabled={busy} onClick={() => { setError(""); setLoadAttempt(value => value + 1); }}>Reload recorded deletions</button> : <p role="status">Checking recorded deletions…</p>)}
    {planLoading && <p role="status">Checking selected materials…</p>}
    {ready && !plan && !planLoading && !planStopped && error && !started && operations.length === 0 && <button className="button" onClick={() => { setPlanLoading(true); setError(""); setPlanAttempt(value => value + 1); }}>Retry checks</button>}
    {validSelection && !plan && operations.length === 0 && <ul className="material-delete-selection">{selection.map(item => <li key={item.id}>{item.materialName}<small>{item.technicalIdentity ?? "Draft material"}</small></li>)}</ul>}
    {plan && operations.length === 0 && <section aria-label="Reviewed deletion plan">
      <h3>{modeLabel(plan.mode)} · {plan.total} materials</h3>
      {plan.warnings.length > 0 && <ul className="material-delete-notes">{plan.warnings.filter(warning => warning !== "PUBLISHED_EXTERNAL_FILES_UNCHANGED").map((warning, index) => <li key={index}>{warningLabels[warning] ?? "This deletion has an additional notice. Check the selected materials before continuing."}</li>)}</ul>}
      <ul className="material-delete-report" aria-label="Selected materials">{plan.items.map(item => <li className="material-delete-record" key={item.material_id}>
        <div className="material-delete-record-name"><strong>{item.material_name}</strong><small>{item.identity ?? "Draft material"}</small></div>
        <dl><div><dt>Source folder</dt><dd>{item.folder_path ?? "No linked folder"}{item.file_count !== null && <small>{item.file_count} files</small>}</dd></div>
          <div><dt>Review</dt><dd className={item.issues.length ? "material-delete-issues" : "material-delete-ready"}>{item.issues.length ? item.issues.map((issue, index) => <p key={`${issue.code}:${index}`}>{issueLabel(issue.code, issue.message)}</p>) : "Ready to delete"}</dd></div>
        </dl>
      </li>)}</ul>
      {!planReady && <p role="alert">These materials cannot be deleted with the selected option. Resolve the listed issues or choose Records only.</p>}
      {planReady && !started && <p className="material-delete-confirmation">Delete materials will remove these records{plan.mode === "RECORD_AND_FILES" ? " and move their source folders into recovery quarantine" : " while keeping their source folders and files"}.</p>}
    </section>}
    {operations.length > 0 && <section aria-label="Recorded deletions"><h3>Recorded deletion operations</h3>
      <p>Recovery continues the entire recorded operation, including any materials outside the current selection. Review every material below.</p>
      {operations.map(operation => <div className="material-delete-operation" key={operation.id}>
        <h4>{modeLabel(operation.mode)} · {operation.items.length} materials</h4><p role="status">{operation.deleted_count} deleted · {statusLabels[operation.status]}</p>
        {operation.mode === "RECORD_AND_FILES" && <p>Source folders are moved to recovery quarantine; retained files are not permanently erased.</p>}
        <ul>{operation.items.map(item => <li key={item.material_id}>{item.material_name}<small>{item.identity ?? "Draft material"} · {statusLabels[item.status]}{item.error_code ? ` · ${issueLabel(item.error_code)}` : ""}</small></li>)}</ul>
        {unfinished(operation) && !unknown && <button className="button" disabled={busy || !admin || !sameSession() || !recoveryAcknowledged} onClick={() => void save(operation)}>Recover recorded deletion</button>}
      </div>)}
      {recoveries.length > 0 && !unknown && <label className="material-delete-ack"><input type="checkbox" checked={recoveryAcknowledged} disabled={busy || !admin} onChange={event => setRecoveryAcknowledged(event.target.checked)} />I reviewed all recorded materials and their deletion modes and approve continuing these operations.</label>}
    </section>}
    </div>
    <footer className="material-delete-actions"><button className="button" disabled={busy || unknown || recoveries.length > 0} onClick={close}>{started ? "Close" : "Cancel"}</button>
      {!started && operations.length === 0 && <button className="button material-delete-confirm" disabled={!admin || busy || !ready || !validSelection || planLoading || planStopped || !planReady || !sameSession()} onClick={() => void save()}>Delete materials</button>}
      {unknown && <button className="button material-delete-confirm" disabled={!admin || busy || !sameSession()} onClick={() => void save()}>Recover same deletion</button>}
    </footer>
  </dialog>;
}
