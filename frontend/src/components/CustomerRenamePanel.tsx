import { useCallback, useEffect, useRef, useState } from "react";
import { directoryClient, type Customer, type CustomerRenameOperation, type CustomerRenameRequest } from "../api/directoryClient";
import { ApiError } from "../api/errors";
import { sessionGeneration } from "../auth/sessionTransport";
import { useNavigationGuard } from "../navigationGuard";

type Packet = { key: string; generation: number; payload?: CustomerRenameRequest; proposalHash?: string; operationId?: string; writeAttempted: boolean };
export function CustomerRenamePanel({ customer, disabled, onChanged, onBusyChange }: { customer: Customer; disabled: boolean; onChanged: () => void; onBusyChange: (busy: boolean) => void }) {
  const [name, setName] = useState(customer.name), [prefix, setPrefix] = useState(""), [renameMaterials, setRenameMaterials] = useState(false);
  const [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false), [error, setError] = useState("");
  const [requestGeneration, setRequestGeneration] = useState<number>();
  const [operations, setOperations] = useState<CustomerRenameOperation[]>([]), [loadError, setLoadError] = useState(false);
  const [loading, setLoading] = useState(true);
  const dialog = useRef<HTMLDialogElement>(null), trigger = useRef<HTMLButtonElement>(null), packet = useRef<Packet | null>(null), sending = useRef(false), alive = useRef(true);
  const refreshOperations = useCallback(() => directoryClient.customerRenameOperations(customer.id).then(items => { if (alive.current) { setOperations(items.filter(item => item.status !== "COMPLETED")); setLoadError(false); } }).catch(() => { if (alive.current) setLoadError(true); }).finally(() => { if (alive.current) setLoading(false); }), [customer.id]);
  useEffect(() => { alive.current = true; void refreshOperations(); return () => { alive.current = false; }; }, [refreshOperations]);
  useEffect(() => { onBusyChange(busy || uncertain); return () => onBusyChange(false); }, [busy, uncertain, onBusyChange]);
  useNavigationGuard(() => packet.current !== null);
  useEffect(() => { if (!busy && !uncertain) return; const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; }; window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn); }, [busy, uncertain]);
  const close = () => { if (sending.current || packet.current) return; dialog.current?.close(); trigger.current?.focus(); };
  const send = async () => {
    const current = packet.current;
    if (!current || sending.current || current.generation !== sessionGeneration()) return;
    sending.current = true; setBusy(true); setError(""); setRequestGeneration(current.generation);
    const recovering = uncertain;
    try {
      if (current.payload && !current.proposalHash) {
        const plan = await directoryClient.customerRenamePlan(customer.id, current.payload);
        if (!alive.current || current.generation !== sessionGeneration()) { packet.current = null; if (alive.current) setError("The session changed. Sign in again before renaming."); return; }
        if (!plan.ready) { packet.current = null; setError(plan.issues.join(" · ") || "This rename is not ready. Resolve the affected material issues first."); return; }
        current.proposalHash = plan.proposalHash;
      }
      current.writeAttempted = true;
      const result = current.operationId ? await directoryClient.resumeCustomerRename(customer.id, current.operationId)
        : await directoryClient.renameCustomer(customer.id, current.payload!, current.proposalHash!, current.key);
      packet.current = null;
      if (!alive.current || current.generation !== sessionGeneration()) return;
      setUncertain(false);
      if (["COMPLETED", "PARTIAL"].includes(result.status)) { dialog.current?.close(); onChanged(); }
      else { setOperations([result]); setError("Some materials still need recovery. Review the result, then resume the saved operation."); }
    } catch (cause) {
      if (!alive.current) return;
      if (!current.writeAttempted || !recovering && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        packet.current = null; setUncertain(false); setError(cause instanceof Error ? cause.message : "The rename could not be prepared.");
      } else { setUncertain(true); setError("The rename result is unknown. Retry this same request before leaving."); }
    } finally { sending.current = false; if (alive.current) setBusy(false); }
  };
  const recover = (operation: CustomerRenameOperation) => { packet.current = { key: crypto.randomUUID(), generation: sessionGeneration(), operationId: operation.id, writeAttempted: false }; dialog.current?.showModal(); void send(); };
  const recovery = operations.some(operation => ["RUNNING", "RECOVERY_REQUIRED"].includes(operation.status));
  const partialHistory = operations.filter(operation => operation.status === "PARTIAL");
  return <div className="customer-rename">
    <button ref={trigger} className="button" disabled={disabled || busy || uncertain || recovery || loadError || loading} onClick={() => { setName(customer.name); setPrefix(""); setRenameMaterials(false); setError(""); dialog.current?.showModal(); }}>Edit name</button>
    {loadError && <p role="alert">Rename history could not be checked. <button className="button" disabled={busy || uncertain} onClick={() => void refreshOperations()}>Reload rename history</button></p>}
    {operations.filter(operation => ["RUNNING", "RECOVERY_REQUIRED"].includes(operation.status)).map(operation => <div key={operation.id} className="panel"><h3>Customer rename requires recovery</h3><p>{operation.completedCount} of {operation.totalCount} materials completed · {operation.status}</p>{operation.failures.length > 0 && <ul>{operation.failures.map(failure => <li key={failure}>{failure}</li>)}</ul>}<button className="button" disabled={disabled || busy || uncertain} onClick={() => recover(operation)}>Resume customer rename</button></div>)}
    {partialHistory.length > 0 && <details className="panel"><summary>Previous partial rename results ({partialHistory.length})</summary><p>These are historical outcomes. A later rename may already have corrected the listed materials.</p><p>To retry remaining materials, choose Edit name, keep the current customer name, enter the current prefix <strong>{customer.folderPrefix}</strong>, and enable Rename existing materials. Already updated materials will be skipped.</p>{partialHistory.map(operation => <div key={operation.id}><p>{operation.completedCount} of {operation.totalCount} materials completed in this earlier operation.</p><ul>{operation.failures.map(failure => <li key={failure}>{failure}</li>)}</ul></div>)}</details>}
    <dialog ref={dialog} className="resource-bulk-dialog customer-name-dialog" aria-labelledby="customer-rename-title" onCancel={event => { event.preventDefault(); close(); }}>
      <h2 id="customer-rename-title">Edit customer name</h2>
      {!recovery && <form className="record-form" onSubmit={event => { event.preventDefault(); if (packet.current || sending.current || disabled || !name.trim()) return; packet.current = { key: crypto.randomUUID(), generation: sessionGeneration(), writeAttempted: false, payload: { name: name.trim(), ...(prefix.trim() ? { folder_prefix: prefix.trim() } : {}), rename_materials: renameMaterials, expected_updated_at: customer.updatedAt } }; void send(); }}>
        <fieldset disabled={busy || uncertain}><legend>Customer identity</legend><div className="directory-fields">
          <label className="directory-field--wide">New customer name<input autoFocus required maxLength={255} value={name} onChange={event => setName(event.target.value)} /></label>
          <label className="directory-field--wide">Prefix for new materials<input maxLength={255} value={prefix} onChange={event => setPrefix(event.target.value)} placeholder="Generate from the new customer name" /></label>
        </div><p>Current material prefix: <strong>{customer.folderPrefix}</strong>. New materials will use the new prefix. Existing materials keep their names unless you choose the option below.</p>
          <label className="customer-rename-option"><input type="checkbox" checked={renameMaterials} onChange={event => setRenameMaterials(event.target.checked)} />Rename existing materials and mark them unpublished</label>
          {renameMaterials && <p>This also renames existing material folders and files. Confirm that the files are not currently in use.</p>}
          <button className="button button--primary" disabled={!name.trim() || disabled}>Confirm customer rename</button>
        </fieldset>
      </form>}
      {error && <p role="alert" className="form-error">{error}</p>}{busy && <p role="status">{recovery ? "Recovering rename…" : "Preparing and applying rename…"}</p>}
      {uncertain && <button className="button" disabled={busy || requestGeneration !== sessionGeneration()} onClick={() => void send()}>Retry same rename request</button>}
      <button className="button" disabled={busy || uncertain} onClick={close}>Close</button>
    </dialog>
  </div>;
}
