import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/errors";
import { identityClient, type IdentityConfirmation, type IdentityPlan } from "../api/identityClient";
import type { Material } from "../api/materialDto";
import { useNavigationGuard } from "../navigationGuard";
import { httpApiClient, type ApiClient } from "../api/client";
import { useSession } from "../auth/context";
import { sessionGeneration } from "../auth/sessionTransport";
import { useRecordCommand } from "../forms/useRecordCommand";
import { PendingRecordSave } from "../forms/PendingRecordSave";

type MaterialNameDialogProps = {
  material: Material; destination?: { parent: string; absolutePath: string }; onClose: () => void; onChanged: () => Promise<boolean>;
  client?: Pick<ApiClient, "updateMaterial">;
};

export function MaterialNameDialog(props: MaterialNameDialogProps) {
  const actor = useSession()?.session.user.id;
  return props.material.isDraft && !props.destination
    ? <DraftMaterialNameDialog key={`${actor ?? "anonymous"}:${props.material.id}:${sessionGeneration()}`} {...props} />
    : <LinkedMaterialNameDialog {...props} />;
}

function DraftMaterialNameDialog({ material, onClose, onChanged, client = httpApiClient }: MaterialNameDialogProps) {
  const actorId = useSession()?.session.user.id;
  const dialog = useRef<HTMLDialogElement>(null), active = useRef(false), generation = useRef(sessionGeneration());
  const [error, setError] = useState(""), [completing, setCompleting] = useState(false);
  const controller = useRecordCommand({ actorId,
    command: { kind: "MATERIAL", action: "UPDATED", targetId: material.id, editorPath: `/materials/${material.id}/edit-name`,
      payload: values => ({ material_name: values.name.trim() }) },
    save: async (values, key) => {
      const sentGeneration = sessionGeneration();
      await client.updateMaterial(material.id, { material_name: values.name.trim() }, key);
      if (sentGeneration !== sessionGeneration()) throw new Error("The account session changed while saving.");
      return { path: `/materials/${material.id}`, message: "Material name saved." };
    },
    onStart: () => setError(""), onFailure: failure => setError(failure.message),
    onSaved: () => {
      if (!active.current || generation.current !== sessionGeneration()) return;
      setCompleting(true);
      void onChanged().catch(() => false).finally(() => {
        if (active.current && generation.current === sessionGeneration()) { setCompleting(false); onClose(); }
      });
    },
  });
  const [name, setName] = useState(controller.ownPacket?.values.name ?? material.materialName.toUpperCase());
  const busy = controller.busy || completing, frozen = busy || controller.packet !== null;
  const ownPending = busy || controller.ownPacket !== null;
  const changed = Boolean(name.trim()) && name.trim() !== material.materialName.trim().toUpperCase();
  useEffect(() => {
    active.current = true;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.showModal();
    return () => { active.current = false; trigger?.focus(); };
  }, []);
  useNavigationGuard(() => ownPending);
  return <dialog ref={dialog} className="confirm-dialog material-name-dialog" aria-labelledby="draft-material-name-title"
    onCancel={event => { event.preventDefault(); if (!ownPending) onClose(); }}>
    <form className="confirm-dialog__body" onSubmit={event => {
      event.preventDefault();
      if (!frozen && changed) void controller.submit({ name });
    }}>
      <h2 id="draft-material-name-title">Edit Name</h2>
      <p>Update the name of this draft material. Its Customer and Main category can be assigned later.</p>
      {error && <p role="alert" className="field-error">{error}</p>}
      {controller.packet && !controller.ownPacket && <p role="alert">Close this dialog and recover the other pending record save before changing this name.</p>}
      <label>Material name<input value={name} maxLength={255} required disabled={frozen}
        onChange={event => { setName(event.target.value.toUpperCase()); setError(""); }} /></label>
      <PendingRecordSave controller={controller} />
      <div className="form-actions">
        <button type="button" className="button" disabled={ownPending} onClick={onClose}>Cancel</button>
        <button type="submit" className="button button--primary" disabled={frozen || !changed}>{busy ? "Saving…" : "Save name"}</button>
      </div>
    </form>
  </dialog>;
}

function LinkedMaterialNameDialog({ material, destination, onClose, onChanged }: MaterialNameDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null), sending = useRef(false), exact = useRef<IdentityConfirmation | null>(null);
  const [name, setName] = useState(material.materialName.toUpperCase()), [plan, setPlan] = useState<IdentityPlan | null>(null);
  const [pending, setPending] = useState(false), [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [activeOperation, setActiveOperation] = useState<string | null>(null);
  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.showModal();
    return () => trigger?.focus();
  }, []);
  useNavigationGuard(() => sending.current || exact.current !== null || activeOperation !== null);
  const busy = pending || uncertain || activeOperation !== null;
  const target = () => {
    if (!material.publishedBrandId || !material.mainCategoryCode || material.isDraft) throw new Error("Complete the material identity first.");
    return { target_brand_id: material.publishedBrandId, main_category_code: material.mainCategoryCode,
      target_parent: destination?.parent ?? material.folderPath?.split("/").slice(0, -1).join("/") ?? "", material_name: destination ? material.materialName : name.trim() };
  };
  const eligible = !material.isDraft && !material.isPublished && Boolean(material.folderPath) &&
    (material.workflowStatus === "IN_PROGRESS" || (!destination && material.workflowStatus === "DONE"));
  const changed = Boolean(name.trim()) && (Boolean(destination) || name.trim() !== material.materialName.trim().toUpperCase());
  const confirm = async () => {
    if (sending.current || (!activeOperation && !exact.current && (!eligible || !changed))) return;
    sending.current = true; setPending(true); setError("");
    try {
      if (!activeOperation && !exact.current) {
        setPlan(null);
        const operations = await identityClient.operations(material.id);
        if (!operations.enabled) { setError("Source changes are disabled in this environment."); return; }
        const active = operations.items.find(item => item.status === "RUNNING" || item.status === "RECOVERY_REQUIRED");
        if (active) { setActiveOperation(active.id); setError("A recorded folder operation must be recovered before making another change."); return; }
        const request = target();
        const prepared = await identityClient.plan(material.id, request);
        setPlan(prepared);
        if (!prepared.ready) { setError("The folder checks found conflicts. Resolve the listed issues before confirming again."); return; }
        exact.current = { ...request, expected_generation: prepared.generation, expected_proposal_hash: prepared.hash,
          idempotency_key: crypto.randomUUID(), reason: destination ? "Move material data folder" : "Rename material", warnings_acknowledged: true };
      }
      const result = activeOperation ? await identityClient.resume(material.id, activeOperation) : await identityClient.confirm(material.id, exact.current!);
      if (result.status === "RUNNING" || result.status === "RECOVERY_REQUIRED") {
        setActiveOperation(result.id); setUncertain(true); setError("The recorded change needs verification. Recover the same operation before continuing."); return;
      }
      exact.current = null; setActiveOperation(null); setUncertain(false);
      if (result.status === "COMPLETED") { await onChanged(); onClose(); }
      else { setPlan(null); setError("The change was rejected or rolled back. Check the material and source folder before confirming again."); }
    } catch (cause) {
      // A rejected retry cannot disprove that the earlier unknown request committed.
      if (!uncertain && !activeOperation && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        exact.current = null; setUncertain(false); setPlan(null);
        const messages: Record<string, string> = {
          PUBLISHED_IDENTITY_BLOCKED: "A published material cannot be renamed or moved. Clear Published first.",
          IDENTITY_REOPEN_REQUIRED: "This change requires an In progress material. Only a name change is available for an unpublished Done material.",
          MATERIAL_IDENTITY_INVALID: "Use a valid material name without folder separators or unsupported characters.",
          IDENTITY_ALREADY_USED: "Another material already uses this name. Choose a different name.",
          IDENTITY_UNCHANGED: "The material name and folder are unchanged.",
        };
        setError(messages[cause.code ?? ""] ?? "The material or source changed. Check the values and confirm again to repeat the source checks.");
      } else if (!exact.current && !activeOperation) {
        setError("The folder checks could not finish. No rename was requested. Check the source connection and confirm again.");
      } else { setUncertain(true); setError("The result could not be verified. Retry the same operation."); }
    } finally { sending.current = false; setPending(false); }
  };
  return <dialog ref={dialog} className="confirm-dialog material-name-dialog" aria-labelledby="material-name-dialog-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="confirm-dialog__body"><h2 id="material-name-dialog-title">{destination ? "Move material data folder" : "Edit Name"}</h2>
      <p>{destination ? "Confirming moves the complete material folder and updates its recorded path." : "Confirming renames the material folder, matching texture maps and references in metadata.json."} Checked resets to no and current source validation must be repeated.</p>
      {!eligible && <p role="status">{material.isPublished ? "Clear Published before renaming or moving this material." : !material.folderPath ? "Link the material folder before renaming or moving it." : "Set Status to In progress before moving this material folder."}</p>}
      {error && <p className="field-error" role="alert">{error}</p>}
      <fieldset disabled={!eligible || busy}><legend>{destination ? "Destination" : "New material name"}</legend>
        {destination ? <p className="folder-absolute-path">{destination.absolutePath}</p> : <label>Material name<input value={name} maxLength={255}
          onChange={event => { setName(event.target.value.toUpperCase()); setPlan(null); setError(""); }} /></label>}
      </fieldset>
      {plan && !plan.ready && <section aria-label="Folder change issues">
        {plan.errors.length > 0 && <ul>{plan.errors.map((item, index) => <li key={index}>{item.code.replaceAll("_", " ")} · {item.path}</li>)}</ul>}
        {plan.warnings.length > 0 && <ul>{plan.warnings.map((item, index) => <li key={index}>{item.code.replaceAll("_", " ")} · {item.path}</li>)}</ul>}
      </section>}
      <div className="form-actions"><button type="button" className="button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="button" className="button button--primary" disabled={pending || (!uncertain && !activeOperation && (!eligible || !changed))} onClick={() => void confirm()}>
          {pending ? "Working…" : uncertain || activeOperation ? "Recover recorded change" : destination ? "Confirm move" : "Confirm rename"}</button></div>
    </div>
  </dialog>;
}
