import { useCallback, useRef, useState } from "react";
import { ApiError } from "../api/errors";
import { packagingPolicyClient as api, policyLabel, policyValues, type PolicyDecision, type PolicyOverride, type PolicyPreview, type PolicySelection } from "../api/packagingPolicyClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ErrorState, LoadingState } from "./PageState";

type Props = { materialId: string; workflowStatus: string; refreshVersion: number; onChanged: () => void };
type Pending = { kind: "select"; body: PolicySelection } | { kind: "override"; body: PolicyOverride };

function PolicyWork({ materialId, workflowStatus, refreshVersion, onChanged }: Props) {
  const role = useSession()?.session.user.role, initialize = role === "ADMIN" || role === "LEADERSHIP";
  const load = useCallback(async () => {
    void refreshVersion;
    const current = await api.current(materialId);
    return { current, observed: current === null ? await api.observed(materialId) : null };
  }, [materialId, refreshVersion]);
  const resource = useResource(load);
  const [preview, setPreview] = useState<PolicyPreview | null>(null);
  const [reason, setReason] = useState(""), [ack, setAck] = useState(false);
  const [pending, setPending] = useState(false), [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const sending = useRef(false), packet = useRef<Pending | null>(null);
  const [history, setHistory] = useState<{ items: PolicyDecision[]; nextBefore: number | null } | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false), [historyError, setHistoryError] = useState(false);

  const inspectOverride = async () => {
    if (sending.current || !resource.data?.current) return;
    sending.current = true; setPending(true); setError(""); setNotice("");
    try {
      const current = resource.data.current;
      setPreview(await api.preview(materialId, current, current.policy === policyValues[0] ? policyValues[1] : policyValues[0]));
      setReason(""); setAck(false);
    } catch { setError("The policy change could not be reviewed. Reload the policy and check your access."); }
    finally { sending.current = false; setPending(false); }
  };
  const submit = async () => {
    if (sending.current) return;
    if (!packet.current) {
      if (!reason.trim() || !ack) return;
      if (preview) packet.current = { kind: "override", body: { idempotency_key: crypto.randomUUID(),
        expected_policy_id: preview.currentId, policy: preview.proposedPolicy,
        expected_preview_hash: preview.previewHash, reason: reason.trim() } };
      else {
        const observed = resource.data?.observed;
        if (!observed?.review.revisionHash || !observed.review.inventoryId || workflowStatus !== "DONE") return;
        packet.current = { kind: "select", body: { idempotency_key: crypto.randomUUID(),
          expected_generation: observed.review.generation, expected_revision_hash: observed.review.revisionHash,
          expected_inventory_id: observed.review.inventoryId, reason: reason.trim() } };
      }
    }
    sending.current = true; setPending(true); setError(""); setNotice("");
    try {
      const request = packet.current;
      if (request.kind === "select") await api.select(materialId, request.body);
      else await api.override(materialId, request.body);
      packet.current = null; setUncertain(false); setPreview(null); setReason(""); setAck(false); setHistory(null);
      setNotice("ZIP policy saved."); resource.retry(); onChanged();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        packet.current = null; setUncertain(false); setPreview(null); resource.retry();
        setError(cause.code === "PACKAGING_POLICY_TIMEZONE_MISMATCH"
          ? "The worker and application disagree on the storage timezone. Ask an administrator to check the configuration."
          : "The decision was rejected. Reload the current source review and policy before trying again.");
      } else {
        setUncertain(true);
        setError("The outcome is unknown. Retry the same policy request before making another decision.");
      }
    } finally { sending.current = false; setPending(false); }
  };
  const loadHistory = async (more = false) => {
    if (historyBusy) return;
    setHistoryBusy(true); setHistoryError(false);
    try {
      const result = await api.history(materialId, more ? history?.nextBefore ?? undefined : undefined);
      setHistory({ items: more ? [...(history?.items ?? []), ...result.items] : result.items, nextBefore: result.nextBefore });
    } catch { setHistoryError(true); }
    finally { setHistoryBusy(false); }
  };
  const current = resource.data?.current, observed = resource.data?.observed;
  const canInitialize = initialize && current === null && !!observed && workflowStatus === "DONE";
  return <section aria-label="ZIP policy details">
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {uncertain && <button className="button button--primary" disabled={pending} onClick={() => void submit()}>Retry same policy request</button>}
    {resource.error ? <ErrorState message="ZIP policy could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading ZIP policy…" /> : <>
      {current ? <dl className="info-list">
        <div><dt>Saved ZIP rule</dt><dd>{policyLabel(current.policy)}</dd></div>
        <div><dt>Decision version</dt><dd>{current.revision}</dd></div>
        <div><dt>Storage timezone</dt><dd>{current.storageTimezone}</dd></div>
        <div><dt>Reason</dt><dd>{current.reason}</dd></div>
        <div><dt>Saved at</dt><dd><time dateTime={current.createdAt}>{current.createdAt}</time></dd></div>
      </dl> : <p>No ZIP policy has been saved.</p>}
      {current && <p>Future packaging uses this saved rule even if the source folder date changes.</p>}
      {current && role === "ADMIN" && !preview && <button className="button" disabled={pending || uncertain} onClick={() => void inspectOverride()}>Review ZIP policy change</button>}
      {!current && (!observed || workflowStatus !== "DONE") && <p>Mark the material as Done and run a successful current technical check before saving its first policy.</p>}
      {!current && observed && <p>Observed rule: {policyLabel(observed.policy)}. Source master {observed.master}, modified {observed.modifiedAt}.</p>}
      {current && role !== "ADMIN" && <p>Only an administrator can change a saved rule.</p>}
      {preview && <section aria-label="ZIP policy change preview"><h3>Review the effect</h3>
        <p>{preview.identity}: {policyLabel(preview.currentPolicy)} → {policyLabel(preview.proposedPolicy)}.</p>
        <p>All current technical, publication and content approvals will need review again. Existing CSV batches and files keep their original contents. Source files are unchanged.</p>
        {preview.isPublished && <p>The material stays published and will be marked as requiring an update.</p>}
      </section>}
      {(canInitialize || preview) && <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <fieldset disabled={pending || uncertain}><legend>{preview ? "Confirm policy change" : "Save first ZIP policy"}</legend>
          <label>Reason for ZIP policy decision<textarea value={reason} maxLength={2000} onChange={(event) => setReason(event.target.value)} /></label>
          <label><input type="checkbox" checked={ack} onChange={(event) => setAck(event.target.checked)} />I reviewed this ZIP rule and its effect.</label>
        </fieldset>
        {!uncertain && <button className="button button--primary" disabled={pending || !reason.trim() || !ack}>{preview ? "Confirm ZIP policy change" : "Save ZIP policy"}</button>}
        {preview && <button className="button" type="button" disabled={pending || uncertain} onClick={() => setPreview(null)}>Cancel policy change</button>}
      </form>}
      <button className="button" disabled={pending || uncertain} onClick={() => { setPreview(null); resource.retry(); }}>Reload ZIP policy</button>
    </>}
    <h3>ZIP policy history</h3>
    <button className="button" disabled={historyBusy} onClick={() => void loadHistory()}>Load policy history</button>
    {historyError && <p role="alert">ZIP policy history could not be loaded.</p>}
    {history && <ol>{history.items.map((item) => <li key={item.id}>Version {item.revision}: {policyLabel(item.policy)} · {item.reason} · {item.createdAt}</li>)}</ol>}
    {history?.nextBefore && <button className="button" disabled={historyBusy} onClick={() => void loadHistory(true)}>Load older policy decisions</button>}
  </section>;
}

export function PackagingPolicyPanel(props: Props) {
  const [activated, setActivated] = useState(false);
  // Keep the form mounted after closing details, including its unknown-outcome retry.
  return <article className="panel catalog-content" aria-label="ZIP packaging policy">
    <details onToggle={(event) => { if (event.currentTarget.open) setActivated(true); }}>
      <summary>ZIP packaging policy</summary>
      <p>Choose the historical date rule before packaging. Saving a rule does not create ZIPs or publish a material.</p>
      {activated && <PolicyWork {...props} />}
    </details>
  </article>;
}
