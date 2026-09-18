import { useEffect, useRef, useState } from "react";
import { notionClient, notionLabels, notionPageId, type NotionComparison } from "../api/notionClient";
import { useResource } from "../api/useResource";
import { ApiError } from "../api/errors";
import { useSession } from "../auth/context";
import type { Company } from "../types";
import { ErrorState, LoadingState } from "./PageState";
import { adoptionRequest, notionAdoptionClient, type NotionAdoptionRequest } from "../api/notionAdoptionClient";
import type { CompanyChange } from "../api/companyHistoryClient";
import type { NotionField } from "../api/notionClient";

type Packet = { comparison: NotionComparison; body: NotionAdoptionRequest };
// Only memory, one pending packet per current actor; never resend on mount.
let retained: { actorId: string; packet: Packet } | null = null;
type Props = { company: Company; onApplied?: (event: CompanyChange) => void; navigate?: (path: string) => void };

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

function Comparison({ company, actorId, onApplied, navigate }: Props & { actorId: string }) {
  const config = useResource(notionClient.configuration), selected = notionPageId(company.notionPageId);
  const initial = retained?.actorId === actorId ? retained.packet : null;
  const packet = useRef<Packet | null>(initial), [pending, setPending] = useState(initial);
  const [comparison, setComparison] = useState<NotionComparison | null>(null), [error, setError] = useState("");
  const [fields, setFields] = useState<NotionField[]>([]), [reason, setReason] = useState(""), [ack, setAck] = useState(false);
  const [notice, setNotice] = useState(""), [uncertain, setUncertain] = useState(!!initial);
  const [busy, setBusy] = useState(false), mounted = useRef(true), sending = useRef(false);
  useEffect(() => {
    mounted.current = true;
    const prevent = (event: BeforeUnloadEvent) => { if (packet.current) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", prevent);
    return () => { mounted.current = false; window.removeEventListener("beforeunload", prevent); };
  }, []);
  const compare = async () => {
    if (!selected || !config.data?.enabled || sending.current || packet.current) return;
    sending.current = true; setBusy(true); setError(""); setComparison(null); setFields([]); setReason(""); setAck(false); setNotice("");
    try {
      const value = await notionClient.compare(company.id, selected, config.data.fields);
      if (mounted.current) setComparison(value);
    } catch (cause) { if (mounted.current) setError(failure(cause)); }
    finally { sending.current = false; if (mounted.current) setBusy(false); }
  };
  const send = async (readOnly = false) => {
    const current = packet.current;
    if (!current || current.comparison.companyId !== company.id || sending.current) return;
    sending.current = true; setBusy(true); setError("");
    const wasUnknown = uncertain;
    const release = () => { if (retained?.packet === current) retained = null; packet.current = null; };
    try {
      const saved = await (readOnly ? notionAdoptionClient.recover : notionAdoptionClient.adopt)(current.comparison, actorId, current.body);
      release();
      if (mounted.current) {
        setPending(null); setUncertain(false); setComparison(null); setFields([]); setReason(""); setAck(false);
        setNotice(`Saved company change ${saved.version}. These are historical values; refresh the company to see its current profile.`);
        onApplied?.(saved);
      }
    } catch (cause) {
      // Once an outcome was unknown, a later denial or absent read is not proof
      // that the original request never committed. Keep its exact packet.
      if (!wasUnknown && !readOnly && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        release();
        if (mounted.current) { setPending(null); setComparison(null); setFields([]); setReason(""); setAck(false);
          setError("The adoption was rejected. Reload this company and compare current values before trying again."); }
      } else if (mounted.current) {
        setUncertain(true);
        setError(readOnly && cause instanceof ApiError && cause.status === 404
          ? "No saved result is visible yet. The original request may still be running. Keep this request and check again or retry it."
          : "The adoption outcome could not be verified. Check the saved result or retry this exact request before starting another change.");
      }
    } finally { sending.current = false; if (mounted.current) setBusy(false); }
  };
  const adopt = () => {
    if (!comparison || !fields.length || !reason.trim() || !ack || sending.current || packet.current) return;
    try {
      const current = { comparison, body: adoptionRequest(comparison, fields, reason) };
      retained = { actorId, packet: current }; packet.current = current; setPending(current); setUncertain(false); void send();
    } catch { setError("Select changed fields and enter a readable reason of at most 2000 characters."); }
  };
  const ownPending = pending?.comparison.companyId === company.id;
  return <div className="notion-comparison">
    <p>Compare the explicitly linked company page with the current database record. Values are read only when you choose Compare.</p>
    {pending && <section aria-label="Pending Notion adoption">
      <h3>Unresolved adoption request</h3>
      <p>Keep this request until its result is known. Returning during this browser session preserves it. After a full reload, inspect company change history.</p>
      <p className="revision-hash">Request: {pending.body.request_key}</p>
      <p>Selected fields: {pending.body.selected_fields.map((field) => notionLabels[field]).join(", ")}</p>
      <p>Reason: {pending.body.reason}</p>
      <ul className="notion-values">{pending.comparison.rows.filter((row) => pending.body.selected_fields.includes(row.field)).map((row) => <li key={row.field}>
        <h4>{notionLabels[row.field]}</h4><dl><div><dt>Reviewed local value</dt><dd>{row.current ?? "Empty"}</dd></div>
          <div><dt>Requested replacement</dt><dd>{row.observed ?? "Empty"}</dd></div></dl>
      </li>)}</ul>
      {ownPending ? <div className="publication-actions">
        <button className="button" disabled={busy} onClick={() => void send(true)}>Check saved adoption result</button>
        <button className="button" disabled={busy} onClick={() => void send()}>Retry same adoption</button>
      </div> : <><p>This request belongs to another company. Resolve it before starting a new adoption.</p>
        {navigate && <button className="button" onClick={() => navigate(`/companies/${pending.comparison.companyId}`)}>Return to company with pending adoption</button>}</>}
    </section>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {config.error ? <ErrorState message="Notion configuration could not be loaded." retry={config.retry} /> : !config.data ? <LoadingState label="Loading Notion configuration…" /> : <>
    {!config.data.enabled && <p role="status">Notion comparison is disabled on the server.</p>}
    {!selected && <p role="status">This company needs a valid Notion page ID. Add the exact page ID in Edit company.</p>}
    {selected && <p className="revision-hash">Linked page: {selected}</p>}
    {config.data.fields.length > 0 && <p>Configured fields: {config.data.fields.map((field) => notionLabels[field]).join(", ")}</p>}
    <button className="button" disabled={busy || !!pending || !selected || !config.data.enabled} onClick={() => void compare()}>{busy ? "Working…" : "Compare with Notion"}</button>
    {comparison && <section aria-label="Company comparison results" aria-live="polite">
      <h3>Company comparison</h3>
      <p>This comparison records values at the time of the read. Notion last edited: <time dateTime={comparison.editedAt}>{new Date(comparison.editedAt).toLocaleString()}</time>.</p>
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
      <fieldset disabled={busy || !!pending}><legend>Adopt selected values into this company</legend>
        <p>Select each value to replace locally, including any selected empty value. The server reads Notion again and rejects changed comparisons.</p>
        {comparison.rows.filter((row) => row.changed).map((row) => <label key={row.field} className="checkbox-label">
          <input type="checkbox" checked={fields.includes(row.field)} onChange={(event) => { setFields((old) => event.target.checked ? [...old, row.field] : old.filter((field) => field !== row.field)); setAck(false); }} />
          Adopt {notionLabels[row.field]}{row.observed === null ? " (clear local value)" : ""}
        </label>)}
        {!comparison.rows.some((row) => row.changed) && <p>All mapped values are already the same.</p>}
        <label>Reason for company change<textarea maxLength={2000} value={reason} onChange={(event) => { setReason(event.target.value); setAck(false); }} /></label>
        <label className="checkbox-label"><input type="checkbox" checked={ack} onChange={(event) => setAck(event.target.checked)} />I reviewed the selected local replacements.</label>
        <button className="button button--primary" disabled={!fields.length || !reason.trim() || !ack} onClick={adopt}>Adopt selected values</button>
      </fieldset>
    </section>}
    </>}
  </div>;
}

export function NotionCompanyPanel({ company, onApplied, navigate }: Props) {
  const auth = useSession(), [open, setOpen] = useState(false);
  if (auth?.session.user.role !== "ADMIN") return null;
  return <details className="panel panel--wide" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Notion company comparison</summary>
    {open && <Comparison key={`${auth.session.user.id}:${company.id}:${company.notionPageId}:${company.updatedAt}`} company={company} actorId={auth.session.user.id} onApplied={onApplied} navigate={navigate} />}
  </details>;
}
