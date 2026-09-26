import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/errors";
import { packagingClient as api, packagingStatusLabel, type PackagingAction, type PackagingCommand, type PackagingJob, type PackagingReservation } from "../api/packagingClient";
import { packagingPolicyClient, policyLabel } from "../api/packagingPolicyClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ErrorState, LoadingState } from "./PageState";
import { useNavigationGuard } from "../navigationGuard";

const PackagedCopy = lazy(() => import("./PackagedCopy").then((module) => ({ default: module.PackagedCopy })));

type Props = { materialId: string; batch?: { id: string; snapshotHash: string }; onChanged?: () => void };
type Packet = { kind: "reserve"; body: PackagingReservation } | { kind: PackagingAction; id: string; body: PackagingCommand };
const active = (job: PackagingJob) => job.status !== "PACKAGED" && job.status !== "REJECTED";
const actions: Record<PackagingAction, string> = { run: "Start packaging", retry: "Retry packaging", reconcile: "Check packaging result", close: "Close packaging job" };

function PackagingWork({ materialId, batch, onChanged }: Props) {
  const role = useSession()?.session.user.role;
  const load = useCallback(() => api.history(materialId), [materialId]);
  const loadPolicy = useCallback(() => batch ? packagingPolicyClient.current(materialId) : Promise.resolve(null), [materialId, batch]);
  const history = useResource(load), policy = useResource(loadPolicy);
  const [older, setOlder] = useState<Awaited<ReturnType<typeof api.history>> | null>(null);
  const [job, setJob] = useState<PackagingJob | null>(null);
  const [commands, setCommands] = useState<Awaited<ReturnType<typeof api.dispatches>> | null>(null);
  const [reason, setReason] = useState(""), [ack, setAck] = useState(false), [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [readBusy, setReadBusy] = useState(false);
  const packet = useRef<Packet | null>(null), sending = useRef(false), mounted = useRef(true), reading = useRef(false);
  useNavigationGuard(() => packet.current !== null);
  useEffect(() => {
    mounted.current = true;
    const prevent = (event: BeforeUnloadEvent) => { if (packet.current) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", prevent);
    return () => { mounted.current = false; window.removeEventListener("beforeunload", prevent); };
  }, []);
  useEffect(() => {
    if (!job || (job.status !== "RUNNING" && !busy)) return;
    let current = true, pending = false;
    const timer = window.setInterval(() => {
      if (pending) return;
      pending = true;
      void api.detail(materialId, job.id).then((value) => { if (current) setJob(value); })
        .catch(() => { /* An explicit refresh reports failures without poll spam. */ }).finally(() => { pending = false; });
    }, 3000);
    return () => { current = false; window.clearInterval(timer); };
  }, [materialId, job, busy]);

  const inspect = async (action: () => Promise<void>) => {
    if (reading.current) return;
    reading.current = true; setReadBusy(true); setError("");
    try { await action(); }
    catch { if (mounted.current) setError("Packaging history could not be refreshed. Check your access and try again."); }
    finally { reading.current = false; if (mounted.current) setReadBusy(false); }
  };
  const openJob = (id: string) => inspect(async () => {
    const current = await api.detail(materialId, id);
    if (mounted.current) { setJob(current); setCommands(null); setAck(false); }
  });
  const refresh = () => inspect(async () => {
    if (job) { const current = await api.detail(materialId, job.id); if (mounted.current) setJob(current); }
    if (mounted.current) { setOlder(null); history.retry(); policy.retry(); }
  });
  const send = async () => {
    if (sending.current || reading.current || !packet.current) return;
    sending.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const pending = packet.current;
      const saved = pending.kind === "reserve" ? await api.reserve(materialId, pending.body) : await api.command(materialId, pending.id, pending.kind, pending.body);
      packet.current = null;
      if (mounted.current) {
        setJob(saved); setUncertain(false); setReason(""); setAck(false); setCommands(null); setOlder(null); history.retry();
        setNotice(pending.kind === "reserve" ? "Job reserved. Start packaging when ready." : "Packaging progress updated."); onChanged?.();
      }
    } catch (cause) {
      if (!uncertain && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        packet.current = null;
        if (mounted.current) {
          setUncertain(false); setAck(false);
          setError(cause.code === "PACKAGING_DISPATCH_BUSY" ? "Another request is still handling this job. Refresh its progress before the next action."
            : "The request was rejected. Refresh progress and check the source, approvals and your access before trying again.");
          history.retry();
        }
      } else if (mounted.current) {
        setUncertain(true); setError("The outcome is unknown. Recover the same request before sending another action.");
      }
    } finally { sending.current = false; if (mounted.current) setBusy(false); }
  };
  const reserve = () => {
    if (sending.current || reading.current || packet.current || !batch || !policy.data || !history.data?.enabled || !reason.trim() || !ack) return;
    packet.current = { kind: "reserve", body: { idempotency_key: crypto.randomUUID(), batch_id: batch.id,
      expected_snapshot_hash: batch.snapshotHash, expected_policy_id: policy.data.id, reason: reason.trim() } }; void send();
  };
  const command = (kind: PackagingAction) => {
    if (sending.current || reading.current || packet.current || !job || !reason.trim() || !ack) return;
    packet.current = { kind, id: job.id, body: { idempotency_key: crypto.randomUUID(), expected_last_dispatch_id: job.lastDispatchId, reason: reason.trim() } }; void send();
  };
  const loadCommands = (more = false) => inspect(async () => {
    if (!job) return;
    const page = await api.dispatches(materialId, job.id, more ? commands?.nextCursor ?? null : null);
    if (mounted.current) setCommands({ ...page, items: more ? [...(commands?.items ?? []), ...page.items] : page.items });
  });
  const page = older ?? history.data, enabled = history.data?.enabled === true;
  const owned = history.data?.items.some(active), frozen = busy || uncertain || readBusy, archived = history.data?.archived === true;
  const canReserve = !!batch && !owned && !archived && enabled && !!policy.data;
  const canAct = !archived && !!job && active(job);
  const confirmed = !!reason.trim() && ack && !frozen;
  return <section aria-label="Packaging job controls">
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {uncertain && <button className="button button--primary" disabled={busy || readBusy} onClick={() => void send()}>Recover same packaging request</button>}
    {history.error ? <ErrorState message="Packaging jobs could not be loaded." retry={history.retry} /> : !history.data ? <LoadingState label="Loading packaging jobs…" /> : <>
      {!enabled && <p>Packaging service is disabled. History and closure of unsent reservations remain available.</p>}
      {archived && <p>This material is archived. Saved packaging history and accepted files remain available.</p>}
      {batch && <p className="revision-hash">Selected CSV batch: {batch.id}</p>}
      {batch && !archived && policy.error && <ErrorState message="Saved ZIP policy could not be loaded." retry={policy.retry} />}
      {batch && !archived && policy.data && <p>Saved ZIP rule: {policyLabel(policy.data.policy)} · {policy.data.storageTimezone}.</p>}
      {batch && !archived && !policy.data && !policy.error && <p>Save the first ZIP policy on the material detail before reserving a job.</p>}
      {owned && <p>An active job owns this material. Open it below to finish or close it.</p>}
      {!batch && !archived && <p>Create a new reservation from a material in a saved CSV batch in Materials → Publication batches.</p>}
      <h3>Packaging history</h3>
      {!page?.items.length && <p>No packaging jobs.</p>}
      <ul>{page?.items.map((value) => <li key={value.id}><button className="button" disabled={frozen} onClick={() => void openJob(value.id)}>
        Open {packagingStatusLabel[value.status]} · {new Date(value.createdAt).toLocaleString()}</button></li>)}</ul>
      {page?.nextCursor && <button className="button" disabled={frozen} onClick={() => void inspect(async () => {
        const next = await api.history(materialId, page.nextCursor); if (mounted.current) setOlder(next);
      })}>Older packaging jobs</button>}
      <button className="button" disabled={busy || readBusy} onClick={() => void refresh()}>Refresh packaging progress</button>
      {job && <section aria-label="Selected packaging job"><h3>{packagingStatusLabel[job.status]}</h3>
        <p className="revision-hash">Job: {job.id}</p><p className="revision-hash">CSV batch: {job.batchId}</p>
        {job.proofSha256 && <p className="revision-hash">Package proof: {job.proofSha256}</p>}
        {job.status === "RUNNING" && <p>Progress refreshes automatically. After an interrupted request, check the packaging result to recover recorded work.</p>}
        {job.status === "RECOVERY_REQUIRED" && <p>The result needs verification. Check the recorded result or ask an administrator to close the job.</p>}
        {job.proofSha256 && job.status !== "PACKAGED" && <p>Retained output is recorded. It has not been accepted as a current package.</p>}
        {(job.terminal === "CLOSING" || job.terminal === "CLOSED") && active(job) && <p>Closure has started. An administrator must finish closing this job.</p>}
        {job.failureCode && <p>Last check: {job.failureCode.replaceAll("_", " ").toLowerCase()}.</p>}
        <button className="button" disabled={readBusy} onClick={() => void loadCommands()}>Load packaging actions</button>
        {commands && <ol>{commands.items.map((entry) => <li key={entry.id} value={entry.ordinal}>{entry.action.toLowerCase()} · {entry.reason} · {entry.observation?.outcome.toLowerCase().replaceAll("_", " ") ?? "awaiting result"}</li>)}</ol>}
        {commands?.nextCursor && <button className="button" disabled={readBusy} onClick={() => void loadCommands(true)}>More packaging actions</button>}
        {job.status === "PACKAGED" && job.proofSha256 && <Suspense fallback={<LoadingState label="Loading local copy controls…" />}>
          <PackagedCopy key={job.id + job.proofSha256} materialId={materialId} job={job} />
        </Suspense>}
      </section>}
      {(canReserve || canAct) && <fieldset disabled={frozen}><legend>Packaging decision</legend>
        <label>Reason for packaging action<textarea value={reason} maxLength={2000} onChange={(event) => setReason(event.target.value)} /></label>
        <label><input type="checkbox" checked={ack} onChange={(event) => setAck(event.target.checked)} />I reviewed the selected batch, ZIP rule and job progress.</label>
        {canReserve && <button className="button" disabled={!confirmed} onClick={reserve}>Reserve packaging job</button>}
        {canAct && <div className="publication-actions">
          {enabled && job.status === "RESERVED" && <button className="button button--primary" disabled={!confirmed} onClick={() => command("run")}>{actions.run}</button>}
          {enabled && job.status === "RETRY_REQUIRED" && job.terminal === "OPEN" && <button className="button" disabled={!confirmed} onClick={() => command("retry")}>{actions.retry}</button>}
          {enabled && job.lastDispatchId && <button className="button" disabled={!confirmed} onClick={() => command("reconcile")}>{actions.reconcile}</button>}
          {role === "ADMIN" && (enabled || job.status === "RESERVED") && <button className="button" disabled={!confirmed} onClick={() => command("close")}>{actions.close}</button>}
        </div>}
        {canAct && role === "ADMIN" && <p>Closing releases this job's reservation after recovery. Start a new job if packaging is needed later.</p>}
      </fieldset>}
    </>}
  </section>;
}

export function PackagingJobsPanel(props: Props) {
  const role = useSession()?.session.user.role;
  const [opened, setOpened] = useState(false);
  if (role !== "ADMIN" && role !== "LEADERSHIP") return null;
  return <article className="panel catalog-content" aria-label="Material packaging">
    <details onToggle={(event) => { if (event.currentTarget.open) setOpened(true); }}><summary>Material packaging</summary>
      <p>Prepare local packages from approved source files. Upload and online publication have separate steps.</p>
      {opened && <PackagingWork {...props} />}
    </details>
  </article>;
}
