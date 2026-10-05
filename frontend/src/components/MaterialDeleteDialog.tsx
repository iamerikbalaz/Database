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
  const [busy, setBusy] = useState(false), [started, setStarted] = useState(false), [unknown, setUnknown] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false), [recoveryAcknowledged, setRecoveryAcknowledged] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null), mounted = useRef(false), sending = useRef(false), finished = useRef(false);
  // Retain this exact packet until a verified receipt: even a later 403 does not prove that the first write failed.
  const exact = useRef<MaterialDeletionConfirmation | null>(null);
  const resuming = useRef<MaterialDeletionResult | null>(null);
  const sameSession = () => generation === sessionGeneration();
  const recoveries = operations.filter(unfinished);
  const locked = busy || started || unknown || recoveries.length > 0;
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
      operationsRef.current = values; setOperations(values); setReady(true); setError("");
    }).catch(() => { if (live) setError("Recorded deletions could not be checked. Reload them before continuing."); });
    return () => { live = false; };
  }, [admin, validSelection, selection, generation, loadAttempt]);

  const review = async () => {
    if (!admin || !sameSession() || !ready || !validSelection || locked || sending.current) return;
    sending.current = true; setBusy(true); setError(""); setPlan(null); setAcknowledged(false);
    try {
      const value = await materialDeletionClient.plan({ mode, materials: selection.map(item => ({ id: item.id, expected_updated_at: item.updatedAt })) });
      if (!mounted.current || !sameSession()) return;
      if (value.mode !== mode || value.total !== selection.length || !sameIds(selection.map(item => item.id), value.items.map(item => item.material_id)) || value.items.some(item => selection.find(row => row.id === item.material_id)?.materialName !== item.material_name)) throw new Error("The returned plan does not match the selected materials. Close and refresh the list.");
      setPlan(value);
    } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : "The deletion plan could not be reviewed."); }
    finally { sending.current = false; if (mounted.current) setBusy(false); }
  };
  const save = async (operation?: MaterialDeletionResult) => {
    if (!admin || !sameSession() || sending.current) return;
    const recovery = operation ?? resuming.current;
    const retry = exact.current !== null || resuming.current !== null;
    if (recovery && !retry && !recoveryAcknowledged) return;
    if (!recovery && !exact.current) {
      if (!plan?.can_apply || !acknowledged || locked || !ready) return;
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
        exact.current = null; setPlan(null); setAcknowledged(false); setStarted(false); setUnknown(false);
        setError("The deletion was rejected before changes: " + cause.code.replaceAll("_", " ") + ". Close and refresh the selection before reviewing again.");
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
  return <dialog ref={dialog} className="confirm-dialog material-delete-dialog" aria-label="Delete selected materials" onCancel={event => { event.preventDefault(); close(); }}>
    <h2>Delete materials</h2>
    <p>{selection.length} explicitly selected material records. Deleted records disappear from Materials and Archived materials.</p>
    <p>Published library copies and files already uploaded to Google Cloud Storage stay unchanged.</p>
    {!admin && <p role="alert">Only administrators can delete materials.</p>}
    {!validSelection && <p role="alert">Select between 1 and 100 distinct materials.</p>}
    {operations.length === 0 && <fieldset disabled={!admin || !ready || !validSelection || locked}><legend>What should be removed?</legend>
      <label><input type="radio" name="material-deletion-mode" checked={mode === "RECORD_ONLY"} onChange={() => { setMode("RECORD_ONLY"); setPlan(null); setAcknowledged(false); }} />Records only <small>Keep all source folders and files in their current location.</small></label>
      <label><input type="radio" name="material-deletion-mode" checked={mode === "RECORD_AND_FILES"} onChange={() => { setMode("RECORD_AND_FILES"); setPlan(null); setAcknowledged(false); }} />Records and source folders <small>Move each entire source folder out of the material library into protected recovery quarantine on the same disk. Files are retained for recovery, not permanently erased.</small></label>
    </fieldset>}
    {error && <p role="alert" className="field-error">{error}</p>}
    {!ready && validSelection && admin && <button className="button" disabled={busy} onClick={() => setLoadAttempt(value => value + 1)}>Reload recorded deletions</button>}
    {validSelection && !plan && operations.length === 0 && <ul className="material-delete-selection">{selection.map(item => <li key={item.id}>{item.materialName}<small>{item.technicalIdentity ?? "Draft material"}</small></li>)}</ul>}
    {plan && operations.length === 0 && <section aria-label="Reviewed deletion plan">
      <h3>{modeLabel(plan.mode)} · {plan.total} materials</h3>
      {plan.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
      <div className="material-delete-report"><table><thead><tr><th>Material</th><th>Source folder</th><th>Review</th></tr></thead><tbody>{plan.items.map(item => <tr key={item.material_id}>
        <td>{item.material_name}<small>{item.identity ?? "Draft material"}</small></td><td>{item.folder_path ?? "No linked folder"}{item.file_count !== null && <small>{item.file_count} files</small>}</td>
        <td>{item.issues.length ? item.issues.map(issue => <p key={issue.code}>{issue.message}</p>) : "Ready"}</td>
      </tr>)}</tbody></table></div>
      {!plan.can_apply && <p role="alert">This plan cannot be applied. Resolve the reported issues and review again.</p>}
      {plan.can_apply && !started && <label className="material-delete-ack"><input type="checkbox" checked={acknowledged} disabled={busy || !admin} onChange={event => setAcknowledged(event.target.checked)} />I reviewed these materials and approve deleting their records{plan.mode === "RECORD_AND_FILES" ? " and moving their source folders into recovery quarantine" : " while keeping their source files"}.</label>}
    </section>}
    {operations.length > 0 && <section aria-label="Recorded deletions"><h3>Recorded deletion operations</h3>
      <p>Recovery continues the entire recorded operation, including any materials outside the current selection. Review every material below.</p>
      {operations.map(operation => <div className="material-delete-operation" key={operation.id}>
        <h4>{modeLabel(operation.mode)} · {operation.items.length} materials</h4><p role="status">{operation.deleted_count} deleted · {operation.status.replaceAll("_", " ")}</p>
        {operation.mode === "RECORD_AND_FILES" && <p>Source folders are moved to recovery quarantine; retained files are not permanently erased.</p>}
        <ul>{operation.items.map(item => <li key={item.material_id}>{item.material_name}<small>{item.identity ?? "Draft material"} · {item.status.replaceAll("_", " ")}{item.error_code ? ` · ${item.error_code.replaceAll("_", " ")}` : ""}</small></li>)}</ul>
        {unfinished(operation) && !unknown && <button className="button" disabled={busy || !admin || !sameSession() || !recoveryAcknowledged} onClick={() => void save(operation)}>Recover recorded deletion</button>}
      </div>)}
      {recoveries.length > 0 && !unknown && <label className="material-delete-ack"><input type="checkbox" checked={recoveryAcknowledged} disabled={busy || !admin} onChange={event => setRecoveryAcknowledged(event.target.checked)} />I reviewed all recorded materials and their deletion modes and approve continuing these operations.</label>}
    </section>}
    <div className="form-actions"><button className="button" disabled={busy || unknown || recoveries.length > 0} onClick={close}>{started ? "Close" : "Cancel"}</button>
      {!started && operations.length === 0 && <button className="button" disabled={!admin || busy || !ready || !validSelection || !sameSession()} onClick={() => void review()}>Review deletion</button>}
      {plan?.can_apply && !started && <button className="button material-delete-confirm" disabled={!admin || busy || !acknowledged || !sameSession()} onClick={() => void save()}>Confirm delete</button>}
      {unknown && <button className="button material-delete-confirm" disabled={!admin || busy || !sameSession()} onClick={() => void save()}>Recover same deletion</button>}
    </div>
  </dialog>;
}
