import { useEffect, useRef, useState } from "react";
import { notionClient, notionLabels, notionPageId, type NotionComparison } from "../api/notionClient";
import { useResource } from "../api/useResource";
import { ApiError } from "../api/errors";
import { useSession } from "../auth/context";
import type { Company } from "../types";
import { ErrorState, LoadingState } from "./PageState";

function failure(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) return "Administrator access is required. Sign in again if your session changed.";
    if (error.code === "NOTION_RATE_LIMITED") return "Notion has temporarily limited requests. Wait before comparing again.";
    if (error.code === "NOTION_BUSY") return "Another Notion comparison is running. Try again when it finishes.";
    if (error.code === "NOTION_DISABLED") return "Notion comparison is disabled on the server.";
    if (error.code === "NOTION_LOCAL_CHANGED" || error.code === "NOTION_LINK_CHANGED" || error.code === "NOTION_LINK_REQUIRED") return "The company or its Notion link changed. Reload this company before comparing again.";
    if (error.code === "NOTION_SCHEMA_CHANGED" || error.code === "NOTION_PAGE_UNAVAILABLE") return "The linked page or its configured fields are unavailable. Ask an administrator to check the mapping.";
  }
  return "The comparison could not be verified. Retry the read or ask an administrator to check the integration.";
}

function Comparison({ company }: { company: Company }) {
  const config = useResource(notionClient.configuration), selected = notionPageId(company.notionPageId);
  const [comparison, setComparison] = useState<NotionComparison | null>(null), [error, setError] = useState("");
  const [busy, setBusy] = useState(false), mounted = useRef(true), sending = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const compare = async () => {
    if (!selected || !config.data?.enabled || sending.current) return;
    sending.current = true; setBusy(true); setError(""); setComparison(null);
    try {
      const value = await notionClient.compare(company.id, selected, config.data.fields);
      if (mounted.current) setComparison(value);
    } catch (cause) { if (mounted.current) setError(failure(cause)); }
    finally { sending.current = false; if (mounted.current) setBusy(false); }
  };
  if (config.error) return <ErrorState message="Notion configuration could not be loaded." retry={config.retry} />;
  if (!config.data) return <LoadingState label="Loading Notion configuration…" />;
  return <div className="notion-comparison">
    <p>Compare the explicitly linked company page with the current database record. Values are read only when you choose Compare.</p>
    {!config.data.enabled && <p role="status">Notion comparison is disabled on the server.</p>}
    {!selected && <p role="status">This company needs a valid Notion page ID. Add the exact page ID in Edit company.</p>}
    {selected && <p className="revision-hash">Linked page: {selected}</p>}
    {config.data.fields.length > 0 && <p>Configured fields: {config.data.fields.map((field) => notionLabels[field]).join(", ")}</p>}
    <button className="button" disabled={busy || !selected || !config.data.enabled} onClick={() => void compare()}>{busy ? "Reading Notion…" : "Compare with Notion"}</button>
    {error && <p role="alert">{error}</p>}
    {comparison && <section aria-label="Company comparison results" aria-live="polite">
      <h3>Company comparison</h3>
      <p>Point-in-time comparison. No values have been applied. Notion last edited: <time dateTime={comparison.editedAt}>{new Date(comparison.editedAt).toLocaleString()}</time>.</p>
      <ul className="notion-values">{comparison.rows.map((row) => <li key={row.field}>
        <h4>{notionLabels[row.field]} <span className="count-pill">{row.changed ? "Different" : "Same"}</span></h4>
        <dl><div><dt>Current database value</dt><dd>{row.current ?? "Empty"}</dd></div>
          <div><dt>Observed Notion value</dt><dd>{row.observed ?? "Empty"}</dd></div></dl>
      </li>)}</ul>
      <details><summary>Comparison details</summary>
        <dl className="info-list revision-hash"><div><dt>Data source</dt><dd>{comparison.dataSourceId}</dd></div>
          <div><dt>Local record digest</dt><dd>{comparison.localHash}</dd></div>
          <div><dt>Observation digest</dt><dd>{comparison.observationHash}</dd></div></dl>
      </details>
    </section>}
  </div>;
}

export function NotionCompanyPanel({ company }: { company: Company }) {
  const auth = useSession(), [open, setOpen] = useState(false);
  if (auth?.session.user.role !== "ADMIN") return null;
  return <details className="panel panel--wide" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Notion company comparison</summary>
    {open && <Comparison key={`${auth.session.user.id}:${company.id}:${company.notionPageId}:${company.updatedAt}`} company={company} />}
  </details>;
}
