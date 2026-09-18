import { useCallback, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../api/errors";
import type { Material } from "../api/materialDto";
import { reviewClient } from "../api/reviewClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ErrorState, LoadingState } from "./PageState";
import { HistoryPages } from "./HistoryPages";

function message(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 409) return "The material changed or the operation is no longer available. Reload its current state before retrying.";
    if (error.status === 422) return "The source could not be verified. Check the folder and scan again after correcting the reported issue.";
    if (error.status === 403) return "Your role does not allow this operation.";
  }
  return "The request could not be completed. Reload the current state or retry the operation.";
}

export function MaterialReviewPanel({ material, onReopened, onScanned }: { material: Material; onReopened: () => Promise<boolean>; onScanned?: () => Promise<boolean> }) {
  const user = useSession()?.session.user, role = user?.role, actor = user?.id;
  const load = useCallback(async () => {
    void actor; // Retire initial history/current-state reads when the account changes.
    const [current, audit] = await Promise.all([reviewClient.current(material.id), reviewClient.audit(material.id)]);
    return { ...current, audit };
  }, [material.id, actor]);
  const resource = useResource(load);
  const [pending, setPending] = useState(false); const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const [reason, setReason] = useState(""); const [opening, setOpening] = useState(false); const [visible, setVisible] = useState(100);
  const request = useRef<{ fingerprint: string; key: string } | null>(null); const sending = useRef(false);
  const run = async (operation: "scan" | "reopen") => {
    if (sending.current || !resource.data) return;
    sending.current = true; setPending(true); setError(""); setNotice("");
    const generation = resource.data.review.generation;
    const fingerprint = JSON.stringify({ operation, generation, reason: operation === "reopen" ? reason.trim() : "" });
    try {
      if (request.current?.fingerprint !== fingerprint) request.current = { fingerprint, key: crypto.randomUUID() };
      if (operation === "scan") {
        await reviewClient.scan(material.id, generation, request.current.key);
        await onScanned?.();
        setNotice("Source inventory saved. This records the observed contents; it does not approve the material.");
      } else {
        await reviewClient.reopen(material.id, generation, request.current.key, reason.trim());
        setOpening(false); setReason("");
        const complete = await onReopened();
        setNotice(complete ? "Material reopened. Previous metadata snapshots remain in its history." : "Material reopened; some detail data could not be refreshed. Reload the material data.");
      }
      request.current = null; resource.retry();
    } catch (cause) {
      setError(message(cause));
      if (cause instanceof ApiError) { request.current = null; resource.retry(); if (operation === "scan") await onScanned?.(); }
      // Unknown transport outcome keeps the same key for an explicit retry.
    } finally { sending.current = false; setPending(false); }
  };
  const reopen = (event: FormEvent) => { event.preventDefault(); if (reason.trim()) void run("reopen"); };
  const manages = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const data = resource.data;
  return <article className="panel material-review" aria-label="Source review">
    <h2>Source inventory and review</h2>
    <p>Inventory records the files observed during the last scan. Changes require a fresh review.</p>
    {notice && <p role="status">{notice}</p>}{error && <p role="alert" className="field-error">{error}</p>}
    {resource.error ? <ErrorState message="Source review could not be loaded." retry={resource.retry} /> : !data ? <LoadingState label="Loading source review…" /> : <>
      <dl className="info-list"><div><dt>Last scan</dt><dd>{data.review.checkedAt ?? "Not scanned"}</dd></div>
        <div><dt>Review state</dt><dd>{data.review.revisionHash ? "Observed revision available" : "A new scan is required"}</dd></div>
        <div><dt>Last issue</dt><dd>{data.review.failureCode ?? "None"}</dd></div></dl>
      {role !== "LEADERSHIP" && <button className="button" disabled={pending || !material.folderPath} onClick={() => void run("scan")}>{pending ? "Working…" : "Scan source inventory"}</button>}
      {!material.folderPath && <p>Link the material folder before scanning.</p>}
      {data.inventory && <details><summary>{data.inventory.entries.length} inventory entries · {data.inventory.totalBytes.toLocaleString()} bytes</summary>
        <p className="revision-hash">Observed revision: {data.review.revisionHash}</p>
        <div className="table-scroll"><table><thead><tr><th>Path</th><th>Kind</th><th>Bytes</th></tr></thead><tbody>
          {data.inventory.entries.slice(0, visible).map((entry) => <tr key={entry.path}><td>{entry.path}</td><td>{entry.kind}</td><td>{entry.size.toLocaleString()}</td></tr>)}
        </tbody></table></div>
        {data.inventory.entries.length > visible && <button className="button" onClick={() => setVisible((value) => value + 100)}>Show next 100 entries</button>}
      </details>}
      {manages && material.workflowStatus === "DONE" && !opening && <button className="button" disabled={pending} onClick={() => setOpening(true)}>Reopen material</button>}
      {opening && <form onSubmit={reopen} aria-label="Reopen material"><h3>Reopen for production</h3>
        <p>This clears the current metadata and source review. Existing snapshots remain available, and any online version remains published.</p>
        <label>Reason for reopening<textarea required maxLength={2000} value={reason} disabled={pending} onChange={(event) => setReason(event.target.value)} /></label>
        <button className="button button--primary" disabled={pending || !reason.trim()}>Confirm reopen</button>
        <button className="button" type="button" disabled={pending} onClick={() => setOpening(false)}>Cancel</button>
      </form>}
      <details><summary>Source review history</summary>
        <HistoryPages scope={material.id} label="source review history" initial={data.audit} load={(after) => reviewClient.audit(material.id, after)}>
          {(items) => <ol>{items.map((item) => <li key={item.id}><strong>{item.eventType.replaceAll("_", " ")}</strong> · <time dateTime={item.createdAt}>{item.createdAt}</time><p>{item.reason}</p></li>)}</ol>}
        </HistoryPages>
      </details>
      <button className="button" disabled={pending} onClick={resource.retry}>Reload source review</button>
    </>}
  </article>;
}
