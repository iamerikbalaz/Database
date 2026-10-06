import { useEffect, useRef, useState } from "react";
import { CUSTOMER_AI_MAX_BYTES, customerAiBriefClient, parseCustomerAiResultsText, type CustomerAiApply, type CustomerAiReviewRow } from "../api/customerAiBriefClient";
import { ApiError } from "../api/errors";
import type { Customer } from "../api/directoryClient";
import { sessionGeneration } from "../auth/sessionTransport";
import { useNavigationGuard } from "../navigationGuard";
import "./MaterialAiBriefDialog.css";

type Row = CustomerAiReviewRow & { selected: boolean; overwriteDescription: boolean; overwriteWebsite: boolean; outcome?: "Saving" | "Saved" | "Rejected" | "Outcome unknown"; message?: string; exact?: CustomerAiApply };
const statuses: Record<CustomerAiReviewRow["status"], string> = {
  READY: "Ready for your review", OVERWRITE_REQUIRED: "Existing values need your approval to replace", STALE_CONTEXT: "Customer changed since the brief was created. Generate a new brief.",
  ALREADY_APPLIED: "Already applied from this JSON file. Later manual edits are preserved.", NEEDS_REVIEW: "The AI requested further review. Resolve its uncertainty before importing again.",
  EMPTY_PROPOSAL: "No description or website was proposed.", MISSING_RESULT: "This customer is missing from the imported results.",
  CUSTOMER_UNAVAILABLE: "This customer is unavailable. No changes will be made.", CUSTOMER_INACTIVE: "This customer is inactive.",
  CUSTOMER_BUSY: "Another customer operation is still in progress.", NO_CHANGES: "The proposed values already match this customer.",
};
function failure(cause: unknown) {
  if (!(cause instanceof ApiError)) return cause instanceof Error ? cause.message : "The Customer AI request could not be completed.";
  if (cause.status === 409) return "The customer changed or the proposal requires further review. Generate a new brief and review the result again.";
  return cause.message;
}
function download(value: object) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  if (blob.size > CUSTOMER_AI_MAX_BYTES) throw new Error("The generated JSON exceeds 5 MiB. Select fewer customers.");
  const url = URL.createObjectURL(blob), anchor = document.createElement("a");
  anchor.href = url; anchor.download = "customer-ai-brief.json"; document.body.append(anchor);
  try { anchor.click(); } finally { anchor.remove(); URL.revokeObjectURL(url); }
}
const consented = (row: Row) => (!row.requires_description_overwrite || row.overwriteDescription) && (!row.requires_website_overwrite || row.overwriteWebsite);

export function CustomerAiBriefDialog({ customers, onClose, onChanged, onBusyChange }: {
  customers?: Customer[]; onClose: () => void; onChanged: () => void; onBusyChange?: (value: boolean) => void;
}) {
  const [selection] = useState(() => customers?.map(customer => ({ ...customer })) ?? []);
  const [standalone] = useState(customers === undefined), [generation] = useState(sessionGeneration);
  const [rows, setRows] = useState<Row[]>([]), rowsRef = useRef<Row[]>([]);
  const [batchId, setBatchId] = useState<string | null>(null), [busy, setBusy] = useState(false), [started, setStarted] = useState(false), [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [skipped, setSkipped] = useState<{ customer_id: string; message: string }[]>([]);
  const dialog = useRef<HTMLDialogElement>(null), fileInput = useRef<HTMLInputElement>(null), mounted = useRef(false), sending = useRef(false), changed = useRef(false);
  const validSelection = selection.length > 0 && selection.length <= 100 && new Set(selection.map(row => row.id)).size === selection.length;
  const sameSession = () => generation === sessionGeneration(), live = () => mounted.current && sameSession();
  const update = (value: Row[]) => { rowsRef.current = value; if (mounted.current) setRows(value); };
  const put = (index: number, value: Row) => { const next = [...rowsRef.current]; next[index] = value; update(next); };
  const pending = () => rowsRef.current.some(row => row.exact);
  const eligible = rows.filter(row => row.applicable), selectedCount = eligible.filter(row => row.selected && consented(row)).length;
  const replacements = [
    [eligible.filter(row => row.requires_description_overwrite).length, "description"],
    [eligible.filter(row => row.requires_website_overwrite).length, "website"],
  ] as const;
  const replaceText = replacements.filter(([count]) => count).map(([count, label]) => `${count} existing ${label}${count === 1 ? "" : "s"}`).join(" and ");
  const locked = busy || started || uncertain;
  useNavigationGuard(() => sending.current || pending());
  useEffect(() => {
    mounted.current = true; const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.showModal();
    return () => { mounted.current = false; trigger?.focus(); };
  }, []);
  useEffect(() => { onBusyChange?.(true); return () => onBusyChange?.(false); }, [onBusyChange]);
  const generate = async () => {
    if (sending.current || locked || !validSelection || !sameSession()) return;
    sending.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const brief = await customerAiBriefClient.generate(selection.map(row => ({ id: row.id, expected_updated_at: row.updatedAt })));
      if (!live()) return;
      setSkipped(brief.skipped);
      if (!brief.items.length) { setNotice("No selected customers are ready for an AI brief. See the reasons below."); return; }
      download(brief);
      setNotice(`JSON brief downloaded for ${brief.items.length} customers. Use its instructions and result template in your AI tool, then import the returned JSON for review.`);
    } catch (cause) { if (live()) setError(failure(cause)); }
    finally { sending.current = false; if (mounted.current) { setBusy(false); if (!sameSession()) setError("Your session changed. Close this dialog and reopen it after signing in."); } }
  };
  const importFile = async (file: File) => {
    if (sending.current || locked || (!standalone && !validSelection) || !sameSession()) return;
    sending.current = true; setBusy(true); setError(""); setNotice(""); update([]); setBatchId(null);
    try {
      if (file.size > CUSTOMER_AI_MAX_BYTES) throw new Error("Choose a JSON file no larger than 5 MiB.");
      const text = await file.text(); if (!live()) return;
      const results = parseCustomerAiResultsText(text);
      const review = await customerAiBriefClient.review(results, standalone ? results.items.map(row => row.customer_id) : selection.map(row => row.id));
      if (!live()) return;
      setBatchId(review.batch_id); update(review.items.map(row => ({ ...row, selected: false, overwriteDescription: false, overwriteWebsite: false })));
      setNotice("Review each description, manufacturer website and cited source. Fields without a proposal keep their current value. Select individual changes or accept all eligible changes below.");
    } catch (cause) { if (live()) setError(failure(cause)); }
    finally { sending.current = false; if (mounted.current) { setBusy(false); if (!sameSession()) setError("Your session changed. Close this dialog and reopen it after signing in."); } }
  };
  const apply = async (all = false) => {
    if (sending.current || !sameSession() || !batchId || (!uncertain && (started || !(all ? eligible.length : selectedCount)))) return;
    sending.current = true; setBusy(true); setStarted(true); setError(""); setNotice(""); setUncertain(false);
    if (!started) update(rowsRef.current.map(row => (all || row.selected) && row.applicable && row.result && (all || consented(row)) ? {
      ...row, selected: true, overwriteDescription: all ? row.requires_description_overwrite : row.overwriteDescription, overwriteWebsite: all ? row.requires_website_overwrite : row.overwriteWebsite,
      exact: { idempotency_key: crypto.randomUUID(), batch_id: batchId, result: row.result,
        overwrite_description: all ? row.requires_description_overwrite : row.overwriteDescription, overwrite_website: all ? row.requires_website_overwrite : row.overwriteWebsite },
    } : row));
    try {
      for (let index = 0; index < rowsRef.current.length; index++) {
        if (!live()) break;
        const row = rowsRef.current[index]; if (!row.exact) continue;
        const recovering = row.outcome === "Outcome unknown";
        put(index, { ...row, outcome: "Saving", message: undefined });
        try {
          await customerAiBriefClient.apply(row.customer_id, row.exact); changed.current = true;
          if (!mounted.current) break;
          if (!sameSession()) throw new Error("Session changed after saving");
          put(index, { ...row, exact: undefined, outcome: "Saved", message: "Saved to the customer profile. Existing unrelated properties remain unchanged." });
        } catch (cause) {
          if (!mounted.current) break;
          if (!recovering && sameSession() && cause instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(cause.status)) {
            put(index, { ...row, exact: undefined, outcome: "Rejected", message: failure(cause) }); continue;
          }
          put(index, { ...row, outcome: "Outcome unknown", message: "The response could not be verified. Retry this exact save before continuing." });
          setUncertain(true); setError(sameSession() ? "Saving stopped because one result is unknown. Retry the same save to recover it and continue." : "Your session changed during saving. The exact request is retained; no further customers were sent."); break;
        }
      }
    } finally {
      sending.current = false;
      if (mounted.current) { setBusy(false); if (!sameSession() && pending()) { setUncertain(true); setError("Your session changed during saving. The exact requests are retained; no further customers were sent."); } }
    }
  };
  const close = () => { if (sending.current || pending()) return; if (changed.current) { changed.current = false; onChanged(); } onClose(); };
  return <dialog ref={dialog} className="material-ai-brief-dialog" aria-label="Customer AI brief" onCancel={event => { event.preventDefault(); close(); }}>
    <h2>{standalone ? "Import Customer AI results" : "Generate Customer AI brief"}</h2>
    <p>{standalone ? "Import a Customer AI results JSON file. Its customer IDs locate the records, even after restarting the application." : `${selection.length} explicitly selected customers. Download a JSON brief for sourced company descriptions and official manufacturer website URLs, then import the AI results for review.`}</p>
    <p>Only reviewed descriptions and websites are saved. Empty proposals keep existing values. Keep the results JSON to resume from Settings → Imports; already applied proposals are recognized and later manual edits are preserved.</p>
    <p>This does not mark customers as published. Saved customer changes use the normal synchronization queue.</p>
    {!standalone && !validSelection && <p role="alert">Select between 1 and 100 distinct customers.</p>}
    <div className="material-ai-brief-tools">
      {!standalone && <button className="button" disabled={locked || !validSelection || !sameSession()} onClick={() => void generate()}>Download JSON brief</button>}
      <button className="button" disabled={locked || (!standalone && !validSelection) || !sameSession()} onClick={() => fileInput.current?.click()}>Import AI results</button>
      <input ref={fileInput} hidden type="file" aria-label="Import Customer AI results" accept=".json,application/json" disabled={locked || (!standalone && !validSelection) || !sameSession()} onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void importFile(file); }} /><small>Customer JSON · up to 5 MiB · 100 customers</small>
    </div>
    {busy && !started && <p role="status">Preparing Customer AI brief or reviewing results…</p>}
    {notice && <p role="status">{notice}</p>}{error && <p role="alert" className="field-error">{error}</p>}
    {skipped.length > 0 && <section aria-label="Customers omitted from the brief"><h3>Not included in the brief</h3><ul>{skipped.map(row => <li key={row.customer_id}>{selection.find(item => item.id === row.customer_id)?.name}: {row.message}</li>)}</ul></section>}
    {rows.length > 0 && <section aria-label="Customer AI review"><p role="status">{selectedCount} selected · {rows.filter(row => row.outcome === "Saved").length} saved · {rows.filter(row => row.outcome === "Rejected").length} rejected</p>
      {rows.map((row, index) => <article className="material-ai-brief-row" key={row.customer_id} aria-label={row.name}><h3>{row.name}</h3>
        <p><strong>{row.outcome ?? statuses[row.status]}</strong>{row.message && <> {row.message}</>}</p>
        <div className="material-ai-brief-descriptions"><div><h4>Current description</h4><p>{row.current_description || "No description"}</p></div><div><h4>Proposed description</h4><p>{row.proposed_description ?? "Keep current description"}</p></div></div>
        <div className="material-ai-brief-descriptions"><div><h4>Current website</h4><p>{row.current_website || "No website"}</p></div><div><h4>Proposed manufacturer website</h4><p>{row.proposed_website ? <a href={row.proposed_website} target="_blank" rel="noopener noreferrer">{row.proposed_website}</a> : "Keep current website"}</p></div></div>
        <div className="material-ai-brief-sources"><h4>Sources</h4>{row.source_urls.length ? <ul>{row.source_urls.map(url => <li key={url}><a href={url} target="_blank" rel="noopener noreferrer">{url}</a></li>)}</ul> : <p>No sources provided.</p>}</div>
        <p><strong>Uncertainty:</strong> {row.needs_review ? "The AI requested further review." : "No uncertainty flagged by the AI."}{row.note && <> {row.note}</>}</p>
        {row.requires_description_overwrite && <label className="checkbox-label"><input type="checkbox" checked={row.overwriteDescription} disabled={locked || !row.applicable} onChange={event => put(index, { ...row, overwriteDescription: event.target.checked, selected: false })} />I approve replacing the existing description for {row.name}.</label>}
        {row.requires_website_overwrite && <label className="checkbox-label"><input type="checkbox" checked={row.overwriteWebsite} disabled={locked || !row.applicable} onChange={event => put(index, { ...row, overwriteWebsite: event.target.checked, selected: false })} />I approve replacing the existing website for {row.name}.</label>}
        <label className="checkbox-label"><input type="checkbox" checked={row.selected} disabled={locked || !row.applicable || !consented(row)} onChange={event => put(index, { ...row, selected: event.target.checked })} />I reviewed the description, website and sources for {row.name} and want to apply these changes.</label>
      </article>)}
    </section>}
    <div className="form-actions"><button className="button" disabled={busy || uncertain} onClick={close}>{started ? "Close" : "Cancel"}</button>
      {rows.length > 0 && !started && <button className="button button--primary" disabled={busy || !sameSession() || !eligible.length} onClick={() => void apply(true)}>Accept all {eligible.length} changes{replaceText ? ` (replace ${replaceText})` : ""}</button>}
      {(rows.length > 0 || uncertain) && <button className="button button--primary" disabled={busy || !sameSession() || (!uncertain && (started || !selectedCount))} onClick={() => void apply()}>{busy && started ? "Saving customer changes…" : uncertain ? "Retry same save and continue" : `Apply ${selectedCount} reviewed changes`}</button>}
    </div>
  </dialog>;
}
