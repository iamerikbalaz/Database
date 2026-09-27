import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/errors";
import { identityClient, type IdentityConfirmation, type IdentityPlan } from "../api/identityClient";
import type { Material } from "../api/materialDto";
import { useNavigationGuard } from "../navigationGuard";

export function MaterialNameDialog({ material, destination, onClose, onChanged }: {
  material: Material; destination?: { parent: string; absolutePath: string }; onClose: () => void; onChanged: () => Promise<boolean>;
}) {
  const dialog = useRef<HTMLDialogElement>(null), sending = useRef(false), exact = useRef<IdentityConfirmation | null>(null);
  const [name, setName] = useState(material.materialName), [plan, setPlan] = useState<IdentityPlan | null>(null);
  const [pending, setPending] = useState(false), [uncertain, setUncertain] = useState(false), [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState("");
  const [activeOperation, setActiveOperation] = useState<string | null>(null);
  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.showModal();
    return () => trigger?.focus();
  }, []);
  useNavigationGuard(() => sending.current || exact.current !== null || activeOperation !== null);
  const busy = pending || uncertain || activeOperation !== null;
  const target = () => ({ target_brand_id: material.publishedBrandId, main_category_code: material.mainCategoryCode,
    target_parent: destination?.parent ?? material.folderPath?.split("/").slice(0, -1).join("/") ?? "", material_name: name.trim() });
  const eligible = material.workflowStatus === "IN_PROGRESS" && !material.isPublished && Boolean(material.folderPath);
  const prepare = async () => {
    if (sending.current) return;
    sending.current = true; setPending(true); setError(""); setPlan(null); setAcknowledged(false);
    try {
      const operations = await identityClient.operations(material.id);
      if (!operations.enabled) { setError("Source changes are disabled in this environment."); return; }
      const active = operations.items.find(item => item.status === "RUNNING" || item.status === "RECOVERY_REQUIRED");
      if (active) { setActiveOperation(active.id); setError("A recorded folder operation must be reconciled first."); return; }
      setPlan(await identityClient.plan(material.id, target()));
    } catch { setError("The folder change could not be prepared. Reload the material and check the source connection."); }
    finally { sending.current = false; setPending(false); }
  };
  const confirm = async () => {
    if (sending.current || (!activeOperation && !exact.current && (!plan?.ready || !acknowledged))) return;
    sending.current = true; setPending(true); setError("");
    try {
      if (!activeOperation) exact.current ??= { ...target(), expected_generation: plan!.generation, expected_proposal_hash: plan!.hash,
        idempotency_key: crypto.randomUUID(), reason: destination ? "Move material data folder" : "Rename material", warnings_acknowledged: acknowledged };
      const result = activeOperation ? await identityClient.resume(material.id, activeOperation) : await identityClient.confirm(material.id, exact.current!);
      if (result.status === "RUNNING" || result.status === "RECOVERY_REQUIRED") {
        setActiveOperation(result.id); setUncertain(true); setError("The recorded change needs verification. Recover the same operation before continuing."); return;
      }
      exact.current = null; setActiveOperation(null); setUncertain(false);
      if (result.status === "COMPLETED") { await onChanged(); onClose(); }
      else { setPlan(null); setAcknowledged(false); setError("The change was rejected or rolled back. Review a new plan before trying again."); }
    } catch (cause) {
      if (!activeOperation && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        exact.current = null; setUncertain(false); setPlan(null); setAcknowledged(false); setError("The material or source changed. Review a fresh plan.");
      } else { setUncertain(true); setError("The result could not be verified. Retry the same operation."); }
    } finally { sending.current = false; setPending(false); }
  };
  return <dialog ref={dialog} className="confirm-dialog material-name-dialog" aria-labelledby="material-name-dialog-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="confirm-dialog__body"><h2 id="material-name-dialog-title">{destination ? "Move material data folder" : "Edit Name"}</h2>
      <p>{destination ? "This moves the complete material folder and updates its recorded path." : "Changing the material name also renames its folder, matching texture maps and references in metadata.json."}</p>
      {!eligible && <p role="status">Reopen the material and clear Published before changing its name or moving its folder.</p>}
      {error && <p className="field-error" role="alert">{error}</p>}
      <fieldset disabled={!eligible || busy}><legend>{destination ? "Destination" : "New material name"}</legend>
        {destination ? <p className="folder-absolute-path">{destination.absolutePath}</p> : <label>Material name<input value={name} maxLength={255}
          onChange={event => { setName(event.target.value); setPlan(null); setAcknowledged(false); }} /></label>}
        <button type="button" className="button" disabled={!name.trim() || (!destination && name.trim() === material.materialName)} onClick={() => void prepare()}>Preview changes</button>
      </fieldset>
      {plan && <section aria-label="Folder change preview"><p><strong>Current folder:</strong> {plan.source.folder}</p><p><strong>New folder:</strong> {plan.target.folder}</p>
        <p>{plan.changes.length} file and folder names will be updated.</p>
        {plan.errors.length > 0 && <ul>{plan.errors.map((item, index) => <li key={index}>{item.code.replaceAll("_", " ")} · {item.path}</li>)}</ul>}
        {plan.warnings.length > 0 && <ul>{plan.warnings.map((item, index) => <li key={index}>{item.code.replaceAll("_", " ")} · {item.path}</li>)}</ul>}
        <details><summary>Show files affected</summary><ul>{plan.changes.map(item => <li key={item.source}><code>{item.source}</code> → <code>{item.target}</code></li>)}</ul></details>
        {plan.ready && <label className="checkbox-label"><input type="checkbox" checked={acknowledged} disabled={busy} onChange={event => setAcknowledged(event.target.checked)} />
          I approve changing the material folder and its files. Current source checks will need to be repeated.</label>}
      </section>}
      <div className="form-actions"><button type="button" className="button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="button" className="button button--primary" disabled={pending || (!uncertain && !activeOperation && (!plan?.ready || !acknowledged))} onClick={() => void confirm()}>
          {pending ? "Working…" : uncertain || activeOperation ? "Recover recorded change" : destination ? "Confirm move" : "Confirm rename"}</button></div>
    </div>
  </dialog>;
}
