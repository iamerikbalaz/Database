import { useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import { identityClient, type IdentityConfirmation, type IdentityOperation, type IdentityPlan } from "../api/identityClient";
import type { Material } from "../api/materialDto";
import { HISTORY_PAGE_SIZE } from "../api/historyPage";
import { sessionGeneration } from "../auth/sessionTransport";
import { useNavigationGuard } from "../navigationGuard";
import "./MaterialBulkNamesDialog.css";

type Row = {
  material: Material; before: string; after: string; status: string; message?: string;
  enabled?: boolean; fromHistory?: boolean; requestedName?: string; plan?: IdentityPlan; exact?: IdentityConfirmation; operation?: IdentityOperation;
};
const active = (operation: IdentityOperation) => ["RUNNING", "RECOVERY_REQUIRED"].includes(operation.status);
function eligible(material: Material) {
  if (material.isDraft || !material.publishedBrandId || !material.mainCategoryCode) return "Assign Customer and Main category before bulk renaming.";
  if (material.isArchived) return "Archived materials cannot be renamed.";
  if (material.isPublished) return "Clear Published before renaming.";
  if (!material.folderPath) return "Link the material data folder before renaming.";
  if (!["IN_PROGRESS", "DONE"].includes(material.workflowStatus)) return "Only In progress or Done materials can be renamed.";
  return null;
}
function unchanged(current: Material, expected: Material) {
  return current.id === expected.id && current.updatedAt === expected.updatedAt && current.materialName === expected.materialName &&
    current.technicalIdentity === expected.technicalIdentity && current.folderPath === expected.folderPath &&
    current.publishedBrandId === expected.publishedBrandId && current.mainCategoryCode === expected.mainCategoryCode &&
    current.sequenceNumber === expected.sequenceNumber && current.workflowStatus === expected.workflowStatus &&
    current.isPublished === expected.isPublished && current.isArchived === expected.isArchived;
}
function matchingPlan(plan: IdentityPlan, material: Material) {
  const parent = material.folderPath!.split("/").slice(0, -1).join("/");
  return plan.source.materialId === material.id && plan.target.materialId === material.id &&
    plan.source.name === material.materialName && plan.source.identity === material.technicalIdentity && plan.source.folder === material.folderPath &&
    plan.source.brandId === material.publishedBrandId && plan.target.brandId === material.publishedBrandId &&
    plan.source.category === material.mainCategoryCode && plan.target.category === material.mainCategoryCode &&
    plan.source.number === material.sequenceNumber && plan.target.number === material.sequenceNumber && !plan.reservesNumber &&
    Boolean(plan.target.name) && plan.target.name === plan.target.name.toUpperCase() && plan.target.folder?.split("/").slice(0, -1).join("/") === parent;
}

export function MaterialBulkNamesDialog({ materials, client, onClose, onChanged }: {
  materials: Material[]; client: ApiClient; onClose: () => void; onChanged: () => void;
}) {
  const [selection] = useState(() => materials.map(material => ({ ...material })));
  const [generation] = useState(sessionGeneration);
  const validSelection = selection.length > 0 && selection.length <= 100 && new Set(selection.map(item => item.id)).size === selection.length;
  const initial = () => selection.map(material => ({ material, before: material.materialName, after: material.materialName, status: "Not reviewed" }));
  const [rows, setRows] = useState<Row[]>(initial), rowsRef = useRef<Row[]>(initial());
  const [find, setFind] = useState(""), [replacement, setReplacement] = useState("");
  const [historyReady, setHistoryReady] = useState(false), [loadAttempt, setLoadAttempt] = useState(0);
  const [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false), [started, setStarted] = useState(false);
  const [reviewed, setReviewed] = useState(false), [acknowledged, setAcknowledged] = useState(false), [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null), sending = useRef(false), changed = useRef(false), mounted = useRef(false);
  const update = (value: Row[]) => { rowsRef.current = value; setRows(value); };
  const put = (index: number, row: Row) => { const value = [...rowsRef.current]; value[index] = row; update(value); };
  const recovery = rows.some(row => row.operation && active(row.operation));
  const locked = busy || uncertain || started || recovery;
  const ready = rows.filter(row => row.status === "Ready").length;
  const sameSession = () => generation === sessionGeneration();
  useNavigationGuard(() => sending.current || rowsRef.current.some(row => row.exact || (row.operation && active(row.operation))));
  useEffect(() => {
    mounted.current = true;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.showModal(); return () => { mounted.current = false; trigger?.focus(); };
  }, []);
  useEffect(() => {
    if (!validSelection) return;
    let live = true;
    void (async () => {
      const loaded: Row[] = [];
      try {
        for (const material of selection) {
          let history = await identityClient.operations(material.id);
          if (!live || generation !== sessionGeneration()) return;
          let operation = history.items.find(active);
          const cursors = new Set<string>();
          while (!operation && history.items.length === HISTORY_PAGE_SIZE) {
            const cursor = history.items.at(-1)!.id;
            if (cursors.has(cursor)) throw new Error("Identity history repeated a page");
            cursors.add(cursor);
            history = await identityClient.operations(material.id, cursor);
            if (!live || generation !== sessionGeneration()) return;
            operation = history.items.find(active);
          }
          loaded.push({ material, before: operation?.source.name ?? material.materialName, after: operation?.target.name ?? material.materialName,
            enabled: history.enabled, fromHistory: Boolean(operation), status: operation ? "Recovery required" : "Not reviewed", operation });
        }
        rowsRef.current = loaded; setRows(loaded); setHistoryReady(true); setError("");
      } catch { if (live) setError("Recorded identity operations could not be checked. Reload them before renaming."); }
    })();
    return () => { live = false; };
  }, [selection, validSelection, generation, loadAttempt]);
  const invalidate = () => {
    setReviewed(false); setAcknowledged(false); setError("");
    update(rowsRef.current.map(row => ({ ...row, after: row.before, status: "Not reviewed", message: undefined, plan: undefined })));
  };
  const review = async () => {
    if (sending.current || locked || !historyReady || !validSelection || !find || !sameSession()) return;
    sending.current = true; setBusy(true); setError(""); setReviewed(false); setAcknowledged(false);
    const search = find.toUpperCase(), replace = replacement.toUpperCase();
    try {
      for (let index = 0; index < rowsRef.current.length; index++) {
        if (!mounted.current || !sameSession()) break;
        const row = rowsRef.current[index], material = row.material;
        const upper = material.materialName.toUpperCase();
        const after = upper.includes(search) ? upper.split(search).join(replace).trim() : material.materialName;
        let next: Row = { ...row, after, plan: undefined, message: undefined };
        if (after === material.materialName) next = { ...next, status: "Unchanged" };
        else if (!after) next = { ...next, status: "Blocked", message: "The replacement would produce an empty material name." };
        else if (eligible(material) || !material.publishedBrandId || !material.mainCategoryCode || !row.enabled) next = { ...next, status: "Blocked", message: eligible(material) ?? "Source changes are disabled." };
        else {
          put(index, { ...next, status: "Reviewing" });
          try {
            const current = await client.getMaterial(material.id);
            if (!mounted.current || !sameSession()) throw new Error("Session changed");
            if (!unchanged(current, material)) throw new Error("The material changed. Close this dialog and reload the selection.");
            const plan = await identityClient.plan(material.id, { target_brand_id: material.publishedBrandId, main_category_code: material.mainCategoryCode,
              target_parent: material.folderPath!.split("/").slice(0, -1).join("/"), material_name: after });
            if (!mounted.current || !sameSession()) throw new Error("Session changed");
            if (!matchingPlan(plan, material)) throw new Error("The returned plan changes more than the material name. Reload before continuing.");
            next = { ...next, after: plan.target.name, requestedName: after, plan,
              status: plan.target.name === material.materialName ? "Unchanged" : plan.ready ? "Ready" : "Blocked",
              message: plan.errors.map(item => item.code.replaceAll("_", " ") + " · " + item.path).join("; ") };
          } catch (cause) {
            next = cause instanceof ApiError && cause.code === "IDENTITY_UNCHANGED"
              ? { ...next, after: material.materialName, status: "Unchanged" }
              : { ...next, status: "Blocked", message: cause instanceof Error ? cause.message : "The source plan could not be reviewed." };
          }
        }
        put(index, next);
        if (!sameSession()) break;
      }
      setReviewed(true);
    } finally { sending.current = false; setBusy(false); }
  };
  const apply = async () => {
    if (sending.current || !sameSession() || (!uncertain && !recovery && (!reviewed || !acknowledged || !ready))) return;
    sending.current = true; setBusy(true); setStarted(true); setError(""); setUncertain(false);
    try {
      for (let index = 0; index < rowsRef.current.length; index++) {
        if (!mounted.current) break;
        let row = rowsRef.current[index];
        if (!row.exact && !(row.operation && active(row.operation)) && row.status !== "Ready") continue;
        if (!sameSession()) { setUncertain(true); setError("The session changed. Sign in as the original author before recovering this batch."); break; }
        const recovering = Boolean(row.exact || row.operation);
        if (!recovering) {
          try {
            const current = await client.getMaterial(row.material.id);
            if (!mounted.current || !sameSession() || !unchanged(current, row.material)) throw new Error("The material changed after review. Reload and review a new selection.");
            if (!row.plan?.ready || !matchingPlan(row.plan, current)) throw new Error("The reviewed source plan no longer matches this material.");
            if (!row.material.publishedBrandId || !row.material.mainCategoryCode) throw new Error("Assign Customer and Main category before bulk renaming.");
            row = { ...row, exact: { target_brand_id: row.material.publishedBrandId, main_category_code: row.material.mainCategoryCode,
              target_parent: row.material.folderPath!.split("/").slice(0, -1).join("/"), material_name: row.requestedName!,
              expected_generation: row.plan.generation, expected_proposal_hash: row.plan.hash, idempotency_key: crypto.randomUUID(),
              reason: "Bulk material name replacement", warnings_acknowledged: true } };
          } catch (cause) { put(index, { ...row, status: "Blocked", message: cause instanceof Error ? cause.message : "Current material could not be checked." }); continue; }
        }
        put(index, { ...row, status: "Applying" });
        try {
          const result = row.operation ? await identityClient.resume(row.material.id, row.operation.id) : await identityClient.confirm(row.material.id, row.exact!);
          changed.current = true;
          if (!sameSession()) throw new Error("Session changed after request");
          if (result.source.materialId !== row.material.id || result.target.materialId !== row.material.id ||
            (row.operation && result.id !== row.operation.id)) throw new Error("The returned receipt does not match this material operation.");
          if (active(result)) {
            put(index, { ...row, operation: result, status: "Recovery required", message: result.failure ?? "The recorded operation needs recovery." });
            setUncertain(true); setError("The batch stopped. Recover the recorded rename before continuing."); break;
          }
          put(index, { ...row, exact: undefined, operation: undefined, status: result.status === "COMPLETED" ? row.fromHistory ? "Recovered" : "Renamed" : result.status === "ROLLED_BACK" ? "Rolled back" : "Rejected", message: result.failure ?? undefined });
        } catch (cause) {
          if (!recovering && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
            put(index, { ...row, exact: undefined, status: "Blocked", message: cause.message });
            continue;
          }
          // Confirmation may persist its operation before an HTTP failure. Keep
          // the exact key and hash even after a later 403/409 during recovery.
          put(index, { ...row, status: "Outcome unknown", message: cause instanceof ApiError ? cause.message : "The response could not be verified." });
          setUncertain(true); setError("The batch stopped because the result is unknown. Retry the same rename safely."); break;
        }
      }
    } finally { sending.current = false; setBusy(false); }
  };
  const close = () => {
    if (sending.current || uncertain || recovery) return;
    if (changed.current) { changed.current = false; onChanged(); }
    onClose();
  };
  return <dialog ref={dialog} className="confirm-dialog material-bulk-names-dialog" aria-label="Edit material names" onCancel={event => { event.preventDefault(); close(); }}>
    <h2>Edit names</h2><p>{selection.length} explicitly selected materials. Changes affect only the material name.</p>
    <p>Confirmation renames source folders, matching texture maps and metadata references. Checked and Automatic file check must be repeated.</p>
    {!validSelection && <p role="alert">Select between 1 and 100 distinct materials.</p>}
    <fieldset disabled={locked || !historyReady || !validSelection}><legend className="sr-only">Name replacement</legend>
      <div className="material-bulk-names-inputs"><label>Replace text<input value={find} maxLength={255} onChange={event => { setFind(event.target.value); invalidate(); }} /></label>
        <label>With<input value={replacement} maxLength={255} onChange={event => { setReplacement(event.target.value); invalidate(); }} /></label></div>
      <small>Case-insensitive, uppercase names. Literal text is replaced everywhere in the material name; customer, number and category stay the same.</small>
    </fieldset>
    {error && <p role="alert" className="field-error">{error}</p>}
    {!historyReady && validSelection && <button className="button" onClick={() => setLoadAttempt(value => value + 1)}>Reload recorded operations</button>}
    {recovery && <p role="alert">A recorded identity operation is unfinished. Review its source and destination below, then recover it before making new name changes.</p>}
    {started && rows.some(row => row.fromHistory) && !recovery && !uncertain && !busy && <p role="status">Recorded operation recovery finished. Close and reopen this dialog with the refreshed materials to review new name changes.</p>}
    <section aria-label="Material name changes"><p role="status">{ready} ready to rename · {rows.filter(row => row.status === "Renamed").length} renamed · {rows.filter(row => row.status === "Blocked").length} blocked</p>
      <div className="material-bulk-names-report"><table><thead><tr><th>Current name</th><th>New name</th><th>Result</th></tr></thead><tbody>{rows.map(row => <tr key={row.material.id}>
        <td>{row.before}<small>{row.material.technicalIdentity}</small></td><td>{row.after}</td><td>{row.status}{row.message && <p>{row.message}</p>}
          {row.plan?.warnings.map((item, index) => <p key={index}>{item.code.replaceAll("_", " ")} · {item.path}</p>)}
          {row.operation && <p><code>{row.operation.source.folder}</code> → <code>{row.operation.target.folder}</code></p>}</td>
      </tr>)}</tbody></table></div>
    </section>
    {reviewed && !started && ready > 0 && <label className="checkbox-label"><input type="checkbox" checked={acknowledged} disabled={busy} onChange={event => setAcknowledged(event.target.checked)} />I reviewed the names and warnings and approve renaming the source folders, maps and metadata.</label>}
    <div className="form-actions"><button className="button" disabled={busy || uncertain || recovery} onClick={close}>{started ? "Close" : "Cancel"}</button>
      {!started && !recovery && <button className="button" disabled={busy || !historyReady || !find || !validSelection || !sameSession()} onClick={() => void review()}>Review name changes</button>}
      {(ready > 0 || uncertain || recovery) && <button className="button button--primary" disabled={busy || !sameSession() || (!uncertain && !recovery && (!reviewed || !acknowledged))} onClick={() => void apply()}>
        {busy ? "Working…" : uncertain || recovery ? "Recover rename and continue" : `Confirm ${ready} renames`}</button>}
    </div>
  </dialog>;
}
