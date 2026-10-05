import { useEffect, useRef, useState } from "react";
import { AI_BRIEF_MAX_BYTES, aiBriefClient, parseAiBriefResultsText, type AiBriefApply, type AiBriefReviewItem } from "../api/aiBriefClient";
import { ApiError } from "../api/errors";
import type { Material } from "../api/materialDto";
import { sessionGeneration } from "../auth/sessionTransport";
import { useNavigationGuard } from "../navigationGuard";
import "./MaterialAiBriefDialog.css";

type Row = AiBriefReviewItem & { selected: boolean; overwrite: boolean; outcome?: "Saving" | "Saved" | "Rejected" | "Outcome unknown"; message?: string; exact?: AiBriefApply };
const statusLabels: Record<AiBriefReviewItem["status"], string> = {
  READY: "Ready for your review", OVERWRITE_REQUIRED: "Existing description needs your approval to replace", STALE_CONTEXT: "Material changed since the brief was created. Generate a new brief.",
  NEEDS_REVIEW: "The AI marked this result as uncertain. Resolve it before importing again.", EMPTY_DESCRIPTION: "No proposed description was provided.", MISSING_RESULT: "This material is missing from the imported results.",
  MATERIAL_IDENTITY_INCOMPLETE: "Complete the material identity before generating a brief.", CUSTOMER_REQUIRED: "Assign a Customer before generating a brief.",
  CUSTOMER_INACTIVE: "The assigned Customer is inactive.", MATERIAL_BUSY: "Another material operation is still in progress.",
};
function failure(cause: unknown, fallback: string) {
  if (!(cause instanceof ApiError)) return fallback;
  if (cause.code === "AI_CONTEXT_CHANGED" || cause.code === "CONTENT_REVISION_CHANGED") return "The material changed after the brief was created. Generate a new brief.";
  if (cause.code === "AI_OVERWRITE_REQUIRED") return "An existing description requires explicit approval to replace.";
  if (cause.code === "AI_RESULT_NEEDS_REVIEW") return "The AI marked this result as uncertain. Resolve it before importing again.";
  if (cause.code === "AI_DESCRIPTION_REQUIRED") return "A proposed description is required.";
  if (cause.code === "AI_RESULT_OUTSIDE_SELECTION") return "The imported results include a material outside this selection.";
  if (cause.status === 409) return "The material or its content changed. Generate a new brief and review the new results.";
  return cause.message;
}
function downloadJson(value: object) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  if (blob.size > AI_BRIEF_MAX_BYTES) throw new Error("The generated JSON exceeds the 5 MiB limit. Select fewer materials.");
  const url = URL.createObjectURL(blob), anchor = document.createElement("a");
  anchor.href = url; anchor.download = "material-ai-brief.json"; document.body.append(anchor);
  try { anchor.click(); } finally { anchor.remove(); URL.revokeObjectURL(url); }
}

export function MaterialAiBriefDialog({ materials, onClose, onChanged, onBusyChange }: {
  materials: Material[]; onClose: () => void; onChanged: () => void; onBusyChange?: (value: boolean) => void;
}) {
  const [selection] = useState(() => materials.map(material => ({ ...material })));
  const [generation] = useState(sessionGeneration);
  const [rows, setRows] = useState<Row[]>([]), rowsRef = useRef<Row[]>([]);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false), [started, setStarted] = useState(false), [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [skipped, setSkipped] = useState<{ material_id: string; message: string }[]>([]);
  const dialog = useRef<HTMLDialogElement>(null), mounted = useRef(false), sending = useRef(false), changed = useRef(false);
  const validSelection = selection.length > 0 && selection.length <= 100 && new Set(selection.map(material => material.id)).size === selection.length;
  const sameSession = () => generation === sessionGeneration();
  const live = () => mounted.current && sameSession();
  const update = (next: Row[]) => { rowsRef.current = next; if (mounted.current) setRows(next); };
  const put = (index: number, next: Row) => { const value = [...rowsRef.current]; value[index] = next; update(value); };
  const pending = () => rowsRef.current.some(row => row.exact);
  const selectedCount = rows.filter(row => row.selected && row.applicable && (!row.requires_overwrite || row.overwrite)).length;
  const locked = busy || started || uncertain;
  useNavigationGuard(() => sending.current || pending());
  useEffect(() => {
    mounted.current = true;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.showModal();
    return () => { mounted.current = false; trigger?.focus(); };
  }, []);
  useEffect(() => { onBusyChange?.(true); return () => onBusyChange?.(false); }, [onBusyChange]);
  const generate = async () => {
    if (sending.current || locked || !validSelection || !sameSession()) return;
    sending.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const brief = await aiBriefClient.generate(selection.map(material => ({ id: material.id, expected_updated_at: material.updatedAt })));
      if (!live()) return;
      setSkipped(brief.skipped);
      if (!brief.items.length) { setNotice("No selected materials are ready for an AI brief. See the reasons below."); return; }
      downloadJson(brief);
      setNotice(`JSON brief downloaded for ${brief.items.length} ${brief.items.length === 1 ? "material" : "materials"}. Use its instructions and result template in your AI tool, then import the returned JSON here.`);
    } catch (cause) {
      if (live()) setError(cause instanceof ApiError ? failure(cause, "The AI brief could not be generated.") : cause instanceof Error ? cause.message : "The AI brief could not be generated.");
    } finally { sending.current = false; if (mounted.current) { setBusy(false); if (!sameSession()) setError("Your session changed. Close this dialog and reopen it after signing in."); } }
  };
  const importFile = async (file: File) => {
    if (sending.current || locked || !validSelection || !sameSession()) return;
    sending.current = true; setBusy(true); setError(""); setNotice(""); update([]); setBatchId(null);
    try {
      if (file.size > AI_BRIEF_MAX_BYTES) throw new Error("Choose a JSON file no larger than 5 MiB.");
      const text = await file.text();
      if (!live()) return;
      const results = parseAiBriefResultsText(text);
      const review = await aiBriefClient.review(results, selection.map(material => material.id));
      if (!live()) return;
      setBatchId(review.batch_id);
      update(review.items.map(row => ({ ...row, selected: false, overwrite: false })));
      setNotice("Review each description and its sources, then select the descriptions you want to save.");
    } catch (cause) {
      if (live()) setError(cause instanceof ApiError ? failure(cause, "The AI results could not be reviewed.") : cause instanceof Error ? cause.message : "The AI results could not be reviewed.");
    } finally { sending.current = false; if (mounted.current) { setBusy(false); if (!sameSession()) setError("Your session changed. Close this dialog and reopen it after signing in."); } }
  };
  const apply = async () => {
    if (sending.current || !sameSession() || !batchId || (!uncertain && (started || !selectedCount))) return;
    sending.current = true; setBusy(true); setStarted(true); setError(""); setNotice(""); setUncertain(false);
    if (!started) update(rowsRef.current.map(row => row.selected && row.applicable && row.result && (!row.requires_overwrite || row.overwrite)
      ? { ...row, exact: { idempotency_key: crypto.randomUUID(), batch_id: batchId, result: row.result, overwrite: row.overwrite } } : row));
    try {
      for (let index = 0; index < rowsRef.current.length; index++) {
        if (!live()) break;
        const row = rowsRef.current[index];
        if (!row.exact) continue;
        const recovering = row.outcome === "Outcome unknown";
        put(index, { ...row, outcome: "Saving", message: undefined });
        try {
          await aiBriefClient.apply(row.material_id, row.exact);
          changed.current = true;
          if (!mounted.current) break;
          if (!sameSession()) throw new Error("Session changed after saving");
          put(index, { ...row, exact: undefined, outcome: "Saved", message: "Saved to Material data for library. You can edit it on the material card. This does not publish the material." });
        } catch (cause) {
          if (!mounted.current) break;
          if (!recovering && sameSession() && cause instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(cause.status)) {
            put(index, { ...row, exact: undefined, outcome: "Rejected", message: failure(cause, "The description could not be saved.") });
            continue;
          }
          put(index, { ...row, outcome: "Outcome unknown", message: "The save response could not be verified. Retry this same save before continuing." });
          setUncertain(true); setError(sameSession() ? "Saving stopped because one result is unknown. Retry the same save to recover it and continue." : "Your session changed during saving. The exact request is retained; no further descriptions were sent.");
          break;
        }
      }
    } finally {
      sending.current = false;
      if (mounted.current) {
        setBusy(false);
        if (!sameSession() && pending()) { setUncertain(true); setError("Your session changed during saving. The exact requests are retained; no further descriptions were sent."); }
      }
    }
  };
  const close = () => {
    if (sending.current || pending()) return;
    if (changed.current) { changed.current = false; onChanged(); }
    onClose();
  };
  return <dialog ref={dialog} className="material-ai-brief-dialog" aria-label="Material AI brief" onCancel={event => { event.preventDefault(); close(); }}>
    <h2>Generate AI brief</h2>
    <p>{selection.length} explicitly selected {selection.length === 1 ? "material" : "materials"}. Download a JSON brief with material names, Customer, categories and public sources. Use the brief in your AI tool and import its JSON results for review.</p>
    <p>Review and apply selected descriptions to save them to Material data for library. You can edit them on the material card. Applying descriptions does not publish the materials.</p>
    {!validSelection && <p role="alert">Select between 1 and 100 distinct materials.</p>}
    <div className="material-ai-brief-tools">
      <button className="button" disabled={locked || !validSelection || !sameSession()} onClick={() => void generate()}>Download JSON brief</button>
      <label>Import AI results<input type="file" aria-label="Import AI results" accept=".json,application/json" disabled={locked || !validSelection || !sameSession()} onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void importFile(file); }} /><small>JSON file, up to 5 MiB</small></label>
    </div>
    {busy && !started && <p role="status">Preparing AI brief or reviewing results…</p>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert" className="field-error">{error}</p>}
    {skipped.length > 0 && <section aria-label="Materials omitted from the brief"><h3>Not included in the brief</h3><ul>{skipped.map(row => <li key={row.material_id}>{selection.find(item => item.id === row.material_id)?.materialName}: {row.message}</li>)}</ul></section>}
    {rows.length > 0 && <section aria-label="AI description review">
      <p role="status">{selectedCount} selected · {rows.filter(row => row.outcome === "Saved").length} saved · {rows.filter(row => row.outcome === "Rejected").length} rejected</p>
      {rows.map((row, index) => <article className="material-ai-brief-row" key={row.material_id} aria-label={row.name}>
        <h3>{row.name}</h3><small>{selection.find(item => item.id === row.material_id)?.technicalIdentity}</small>
        <p><strong>{row.outcome ?? statusLabels[row.status]}</strong>{row.message && <> {row.message}</>}</p>
        <div className="material-ai-brief-descriptions"><div><h4>Current description</h4><p>{row.current_description || "No description"}</p></div><div><h4>Proposed description</h4><p>{row.proposed_description || "No description provided"}</p></div></div>
        <div className="material-ai-brief-sources"><h4>Sources</h4>{row.source_urls.length ? <ul>{row.source_urls.map((url, sourceIndex) => <li key={`${sourceIndex}-${url}`}><a href={url} target="_blank" rel="noopener noreferrer">{url}</a></li>)}</ul> : <p>No sources provided.</p>}</div>
        <p><strong>Uncertainty:</strong> {row.needs_review ? "The AI requested further review." : "No uncertainty flagged by the AI."}{row.note && <> {row.note}</>}</p>
        {row.requires_overwrite && <label className="checkbox-label"><input type="checkbox" checked={row.overwrite} disabled={locked || !row.applicable} onChange={event => put(index, { ...row, overwrite: event.target.checked, selected: false })} />I approve replacing the existing description for {row.name}.</label>}
        <label className="checkbox-label"><input type="checkbox" checked={row.selected} disabled={locked || !row.applicable || (row.requires_overwrite && !row.overwrite)} onChange={event => put(index, { ...row, selected: event.target.checked })} />I reviewed the description and sources for {row.name} and want to apply it.</label>
      </article>)}
    </section>}
    <div className="form-actions">
      <button className="button" disabled={busy || uncertain} onClick={close}>{started ? "Close" : "Cancel"}</button>
      {(rows.length > 0 || uncertain) && <button className="button button--primary" disabled={busy || !sameSession() || (!uncertain && (started || !selectedCount))} onClick={() => void apply()}>{busy && started ? "Saving descriptions…" : uncertain ? "Retry same save and continue" : `Apply ${selectedCount} reviewed ${selectedCount === 1 ? "description" : "descriptions"}`}</button>}
    </div>
  </dialog>;
}
