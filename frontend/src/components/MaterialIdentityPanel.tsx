import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import { identityClient, type IdentityConfirmation, type IdentityOperation, type IdentityPlan } from "../api/identityClient";
import type { Material } from "../api/materialDto";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ErrorState, LoadingState } from "./PageState";
import { categoryLabel, materialCategories } from "../data/materialCategories";
import { useNavigationGuard } from "../navigationGuard";
import { HistoryPages } from "./HistoryPages";

const labels: Record<string, string> = {
  COMPLETED: "Identity updated", RUNNING: "Outcome pending", ROLLED_BACK: "Source restored", REJECTED: "Rejected before source changes",
  RECOVERY_REQUIRED: "Recovery required", SOURCE_METADATA_MISSING: "Root metadata.txt is missing",
  IDENTITY_TARGET_COLLISION: "The destination already contains an item with this name",
  METADATA_REWRITE_UNSUPPORTED: "Source metadata cannot be rewritten safely", METADATA_UNMAPPED_REFERENCE: "Metadata contains an unsupported identity reference",
};

export function MaterialIdentityPanel({ material, client, onChanged, initialBrand, initialCategory, onBusyChange }: { material: Material; client: ApiClient; onChanged: () => Promise<boolean>; initialBrand?: string; initialCategory?: string; onBusyChange?: (busy: boolean) => void }) {
  const user = useSession()?.session.user, role = user?.role, actor = user?.id;
  const allowed = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const load = useCallback(async () => {
    void actor;
    const [operations, brands] = await Promise.all([identityClient.operations(material.id), allowed ? client.getBrands() : Promise.resolve([])]);
    return { operations, brands };
  }, [material.id, client, allowed, actor]);
  const resource = useResource(load);
  const [targetBrand, setTargetBrand] = useState(initialBrand ?? material.publishedBrandId);
  const [category, setCategory] = useState(initialCategory ?? material.mainCategoryCode);
  const [parent, setParent] = useState(material.folderPath?.split("/").slice(0, -1).join("/") ?? "");
  const [proposal, setProposal] = useState<IdentityPlan | null>(null);
  const [reason, setReason] = useState(""); const [acknowledged, setAcknowledged] = useState(false);
  const [pending, setPending] = useState(false); const sending = useRef(false);
  const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const [uncertain, setUncertain] = useState(false);
  useEffect(() => { onBusyChange?.(pending || uncertain); return () => onBusyChange?.(false); }, [pending, uncertain, onBusyChange]);
  const confirmation = useRef<IdentityConfirmation | null>(null);
  useNavigationGuard(() => sending.current || confirmation.current !== null);
  const active = resource.data?.operations.items.find((item) => item.status === "RUNNING" || item.status === "RECOVERY_REQUIRED");
  const eligible = allowed && material.workflowStatus === "IN_PROGRESS" && !material.isPublished && Boolean(material.folderPath) && !active;
  const target = () => ({ target_brand_id: targetBrand, main_category_code: category.trim().toUpperCase(), target_parent: parent.trim() });
  const invalidate = () => { setProposal(null); setAcknowledged(false); setNotice(""); };
  const complete = async (result: IdentityOperation) => {
    confirmation.current = null; setUncertain(false); setProposal(null); setAcknowledged(false);
    resource.retry();
    const refreshed = await onChanged();
    setNotice((labels[result.status] ?? result.status) + (refreshed ? "." : ". Reload the material to refresh its identity."));
  };
  const run = async (kind: "plan" | "confirm" | "resume", operationId?: string) => {
    if (sending.current) return;
    sending.current = true; setPending(true); setError(""); setNotice("");
    try {
      if (kind === "plan") { setProposal(await identityClient.plan(material.id, target())); setAcknowledged(false); }
      else if (kind === "resume" && operationId) await complete(await identityClient.resume(material.id, operationId));
      else if (kind === "confirm" && proposal) {
        confirmation.current ??= { ...target(), expected_generation: proposal.generation, expected_proposal_hash: proposal.hash,
          idempotency_key: crypto.randomUUID(), reason: reason.trim(), warnings_acknowledged: acknowledged };
        await complete(await identityClient.confirm(material.id, confirmation.current));
      }
    } catch (cause) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        confirmation.current = null; setUncertain(false); setProposal(null); setAcknowledged(false); resource.retry();
        setError(cause.status === 403 ? "Your role no longer allows this operation." : "The material or plan changed, or a requirement was not met. Reload and review a fresh plan.");
      } else {
        setUncertain(kind === "confirm");
        setError(kind === "plan" ? "The source plan could not be prepared. Check worker availability and retry." : "The operation outcome is unknown. Retry the same request or reload its recorded status before taking further action.");
      }
    } finally { sending.current = false; setPending(false); }
  };
  return <article className="panel material-review" aria-label="Material identity">
    <h2>Controlled identity changes</h2>
    <p>Move a linked folder, change its category or transfer it to another brand. The material UUID and project stay the same. Review the exact changes before confirmation.</p>
    {error && <p role="alert" className="field-error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {resource.error ? <ErrorState message="Identity operations could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading identity operations…" /> : <>
      {active && <section aria-label="Active identity operation"><h3>{labels[active.status]}</h3>
        <p>The material is locked while this operation is reconciled. Reloading or retrying uses the same recorded operation.</p>
        <p><code>{active.source.folder}</code> → <code>{active.target.folder}</code></p>
        {active.status === "RECOVERY_REQUIRED" && <p>Conflicting source data needs operator review. Recovery preserves external files and attempts to restore the original source.</p>}
        {allowed && <button className="button" disabled={pending || !resource.data.operations.enabled} onClick={() => void run("resume", active.id)}>Reconcile recorded operation</button>}
      </section>}
      {allowed && !material.folderPath && <p>Link a source folder before planning an identity change.</p>}
      {allowed && material.workflowStatus === "DONE" && <p>Reopen this material before changing its identity.</p>}
      {allowed && material.isPublished && <p>Published identity changes await verification of the online importer contract.</p>}
      {eligible && <>
        {!resource.data.operations.enabled && <p>Source changes are disabled in this environment. Read-only planning remains available.</p>}
        <fieldset disabled={pending || uncertain}><legend>Target identity</legend>
          <label>Target brand<select value={targetBrand} onChange={(event) => { setTargetBrand(event.target.value); invalidate(); }}>
            {resource.data.brands.filter((brand) => brand.isActive).map((brand) => <option key={brand.id} value={brand.id}>{brand.name}</option>)}
          </select></label>
          <label>Target category code<select value={category} onChange={(event) => { setCategory(event.target.value); invalidate(); }}>{!materialCategories.some(c => c.code === category) && <option value={category}>{categoryLabel(category)}</option>}{materialCategories.map(c => <option key={c.code} value={c.code}>{categoryLabel(c.code)}</option>)}</select></label>
          <label>Destination parent folder<input value={parent} maxLength={1792} onChange={(event) => { setParent(event.target.value); invalidate(); }} /></label>
          <p>Use an existing relative parent folder. An empty value means the materials root.</p>
          <p>Remove collection assignments in Publication content before transferring to another brand.</p>
          <button className="button" disabled={!category.trim() || !targetBrand} onClick={() => void run("plan")}>Preview identity changes</button>
        </fieldset>
        {proposal && <section aria-label="Identity change preview"><h3>Review the proposed changes</h3>
          <dl className="info-list"><div><dt>Current folder</dt><dd><code>{proposal.source.folder}</code></dd></div>
            <div><dt>New folder</dt><dd><code>{proposal.target.folder}</code></dd></div>
            <div><dt>New identity</dt><dd>{proposal.target.identity}</dd></div></dl>
          {proposal.reservesNumber && <p>Confirmation reserves number {proposal.target.number} in the target brand. This number stays used even if the source operation fails.</p>}
          <p>Confirmation invalidates current checks and approvals. Run technical checks again after the change.</p>
          {proposal.errors.length > 0 && <section aria-label="Blocking identity findings"><h4>Resolve before confirmation</h4><ul>{proposal.errors.map((item, index) => <li key={index}>{labels[item.code] ?? item.code.replaceAll("_", " ")} · {item.path}</li>)}</ul></section>}
          {proposal.warnings.length > 0 && <section aria-label="Identity warnings"><h4>Warnings</h4><ul>{proposal.warnings.map((item, index) => <li key={index}>{labels[item.code] ?? item.code.replaceAll("_", " ")} · {item.path}</li>)}</ul></section>}
          <details><summary>File and directory renames ({proposal.changes.length})</summary><div className="table-scroll"><table><thead><tr><th>Current path</th><th>New path</th><th>SHA-256</th></tr></thead>
            <tbody>{proposal.changes.map((item) => <tr key={item.source}><td>{item.source}</td><td>{item.target}</td><td><code>{item.hash ?? "Directory"}</code></td></tr>)}</tbody></table></div></details>
          <p>Metadata fields to update: {proposal.metadata.fields.join(", ") || "None"}</p>
          <details><summary>Revision and metadata hashes</summary><dl><dt>Source revision</dt><dd><code>{proposal.sourceHash}</code></dd><dt>Plan</dt><dd><code>{proposal.hash}</code></dd>
            <dt>Metadata before</dt><dd><code>{proposal.metadata.beforeHash ?? "Missing"}</code></dd><dt>Metadata after</dt><dd><code>{proposal.metadata.afterHash ?? "Missing or blocked"}</code></dd></dl></details>
          {proposal.ready && resource.data.operations.enabled && <>
            <fieldset disabled={pending || uncertain}><legend>Confirm this plan</legend>
              <label>Reason for identity change<textarea value={reason} maxLength={2000} onChange={(event) => setReason(event.target.value)} /></label>
              {proposal.warnings.length > 0 && <label><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />I reviewed the identity warnings.</label>}
            </fieldset>
            <button className="button button--primary" disabled={pending || !reason.trim() || (proposal.warnings.length > 0 && !acknowledged)} onClick={() => void run("confirm")}>{uncertain ? "Retry same confirmation" : "Confirm identity change"}</button>
          </>}
        </section>}
      </>}
      <details><summary>Recorded identity operations</summary>
        <HistoryPages scope={material.id} label="identity history" initial={resource.data.operations.items} load={async (after) => (await identityClient.operations(material.id, after)).items}>
          {(items) => <ul>{items.map((item) => <li key={item.id}>
            <strong>{labels[item.status]}</strong> · {item.source.identity} → {item.target.identity} · {item.reason} · <time dateTime={item.createdAt}>{item.createdAt}</time>
          </li>)}</ul>}
        </HistoryPages>
      </details>
      <button className="button" disabled={pending} onClick={() => { resource.retry(); void onChanged(); }}>Reload identity status</button>
    </>}
  </article>;
}
