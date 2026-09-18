import { useCallback, useState } from "react";
import { companyHistoryClient, companyHistoryLabels } from "../api/companyHistoryClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ErrorState, LoadingState } from "./PageState";
import type { Company } from "../types";

const labels = { CREATED: "Company created", UPDATED: "Company updated", NOTION_ADOPTED: "Reviewed Notion values adopted" };
const display = (value: unknown) => value === undefined ? "Not recorded" : value === null ? "Empty" : typeof value === "boolean" ? value ? "Yes" : "No" : String(value);

function History({ companyId }: { companyId: string }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const load = useCallback(() => companyHistoryClient.history(companyId, cursor), [companyId, cursor]);
  const result = useResource(load);
  return <div className="company-history">
    <p>History covers application changes recorded after auditing was enabled. Earlier changes are not reconstructed.</p>
    <p>Saved values describe each past change. The company profile shows the current record.</p>
    {result.error ? <ErrorState message="Company history could not be verified." retry={result.retry} /> : !result.data ? <LoadingState label="Loading company history…" /> : <>
      {!result.data.items.length && <p>No recorded company changes.</p>}
      <ol className="company-history-list">{result.data.items.map((item) => <li key={item.id}>
        <details><summary>Change {item.version} · {labels[item.action]} · {new Date(item.createdAt).toLocaleString()}</summary>
          <p className="revision-hash">Actor: {item.actorId}</p>
          {item.reason && <p>{item.reason}</p>}
          <ul className="notion-values">{item.changed.map((field) => <li key={field}><h4>{companyHistoryLabels[field]}</h4><dl>
            <div><dt>Before this change</dt><dd>{display(item.before?.[field])}</dd></div>
            <div><dt>After this change</dt><dd>{display(item.after[field])}</dd></div>
          </dl></li>)}</ul>
          <details><summary>History evidence</summary><p className="revision-hash">Event: {item.id}</p>
            <p className="revision-hash">Before digest: {item.beforeHash}</p><p className="revision-hash">After digest: {item.afterHash}</p></details>
        </details>
      </li>)}</ol>
    </>}
    <div className="publication-actions"><button className="button" disabled={!result.data && !result.error} onClick={() => { if (cursor) setCursor(null); else result.retry(); }}>Latest changes</button>
      <button className="button" disabled={!result.data?.nextCursor} onClick={() => setCursor(result.data!.nextCursor)}>Older changes</button></div>
  </div>;
}

export function CompanyHistoryPanel({ company }: { company: Company }) {
  const auth = useSession(), [open, setOpen] = useState(false);
  if (auth?.session.user.role !== "ADMIN") return null;
  return <details className="panel panel--wide" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Company change history</summary>
    {open && <History key={`${auth.session.user.id}:${company.id}:${company.updatedAt}`} companyId={company.id} />}
  </details>;
}
