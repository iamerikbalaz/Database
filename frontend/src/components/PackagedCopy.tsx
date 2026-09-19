import { useEffect, useMemo, useRef, useState } from "react";
import { packagingRetirementClient as api, type CopyJob, type Retirement, type RetirementRecovery, type RetirementRequest } from "../api/packagingRetirementClient";
import { useSession } from "../auth/context";
import { PackagingArtifacts } from "./PackagingArtifacts";

type Props = { materialId: string; job: CopyJob };
type Page = Awaited<ReturnType<typeof api.read>>;
type Packet = { kind: "remove"; body: RetirementRequest } | { kind: "recover"; body: RetirementRecovery };
const labels: Record<Retirement["status"], string> = {
  RESERVED: "Local copy removal reserved", RUNNING: "Local copy removal awaiting a result",
  RECOVERY_REQUIRED: "Local copy removal needs recovery", REMOVED: "Local copy removed",
};

function CopyControls({ materialId, job, actorId, admin, pending }: Props & { actorId: string; admin: boolean; pending: boolean }) {
  const [page, setPage] = useState<Page | null>(null), [busy, setBusy] = useState(true), [error, setError] = useState("");
  const [reason, setReason] = useState(""), [ack, setAck] = useState(false), [uncertain, setUncertain] = useState(false);
  const [actions, setActions] = useState<Awaited<ReturnType<typeof api.history>> | null>(null);
  const snapshot = useRef<Page | null>(null), packet = useRef<Packet | null>(null), working = useRef(false), epoch = useRef(0);
  useEffect(() => {
    const generation = ++epoch.current;
    const prevent = (event: BeforeUnloadEvent) => { if (packet.current) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", prevent);
    void Promise.resolve().then(async () => {
      if (epoch.current !== generation) return;
      try {
        const value = await api.read(materialId, job);
        if (epoch.current === generation) { snapshot.current = value; setPage(value); }
      } catch { if (epoch.current === generation) setError("Local copy availability could not be verified. Check your access and refresh."); }
      finally { if (epoch.current === generation) setBusy(false); }
    });
    return () => { epoch.current = generation + 1; window.removeEventListener("beforeunload", prevent); };
  }, [materialId, job]);

  function accept(value: Page) {
    const previous = snapshot.current?.retirement;
    if (previous && (value.retirement?.id !== previous.id || (previous.status === "REMOVED" && value.retirement.status !== "REMOVED"))) {
      throw new Error("Removal evidence changed");
    }
    snapshot.current = value; setPage(value);
    if (value.retirement) { packet.current = null; setUncertain(false); setReason(""); setAck(false); }
  }
  async function inspect(history = false, more = false) {
    if (working.current || busy || pending) return;
    working.current = true; setBusy(true); setError(""); const generation = epoch.current;
    try {
      if (history) {
        const next = await api.history(materialId, job, more ? actions?.nextCursor ?? null : null);
        const items = more ? [...(actions?.items ?? []), ...next.items] : next.items;
        if (new Set(items.map((item) => item.id)).size !== items.length || items.some((item, index) => index > 0 && item.ordinal <= items[index - 1].ordinal)) throw new Error("Invalid removal history");
        if (epoch.current === generation) setActions({ ...next, items });
      } else {
        const next = await api.read(materialId, job);
        if (epoch.current === generation) accept(next);
      }
    } catch { if (epoch.current === generation) setError("Recorded removal could not be checked. Your pending request is preserved; refresh your access and try again."); }
    finally { if (epoch.current === generation) { working.current = false; setBusy(false); } }
  }
  async function send() {
    if (working.current || busy || pending || !admin || !packet.current) return;
    working.current = true; setBusy(true); setError(""); const generation = epoch.current, sent = packet.current;
    try {
      const result = sent.kind === "remove" ? await api.retire(materialId, job, actorId, sent.body) : await api.recover(materialId, job, sent.body);
      if (epoch.current === generation) { accept({ enabled: snapshot.current?.enabled ?? false, retirement: result }); setActions(null); }
    } catch {
      if (epoch.current === generation) {
        setUncertain(true);
        setError("The removal outcome is unknown. Check the recorded removal or retry the same request. Do not start another removal.");
      }
    } finally { if (epoch.current === generation) { working.current = false; setBusy(false); } }
  }
  function decide() {
    if (!page?.enabled || !reason.trim() || !ack || packet.current || busy || pending || !admin || page.retirement?.status === "REMOVED" || !job.lastObservationId || !job.proofSha256) return;
    const common = { idempotency_key: crypto.randomUUID(), expected_proof_sha256: job.proofSha256,
      acknowledgement: "REMOVE_LOCAL_COPY" as const, reason: reason.trim() };
    packet.current = page.retirement ? { kind: "recover", body: { ...common, expected_retirement_id: page.retirement.id,
      expected_last_dispatch_id: page.retirement.lastDispatchId } } : { kind: "remove", body: { ...common, expected_observation_id: job.lastObservationId } };
    void send();
  }
  const retired = page?.retirement, disabled = busy || pending, frozen = disabled || uncertain;
  return <section className="packaged-copy" aria-label="Local packaged copy">
    <h4>Local packaged copy</h4>
    {error && <p role="alert">{error}</p>}
    {!page && busy && <p role="status">Checking local copy availability…</p>}
    <button className="button" disabled={disabled} onClick={() => void inspect()}>Check recorded removal</button>
    {uncertain && admin && <button className="button" disabled={disabled} onClick={() => void send()}>Retry same removal request</button>}
    {retired && <>
      <p role="status">{labels[retired.status]}</p>
      <p>Downloads and new staging jobs are unavailable for this copy. Source files, packaging proof and publication history are preserved.</p>
      <p className="revision-hash">Removal: {retired.id}</p>
      <p className="revision-hash">Requested by: {retired.actorId} · {retired.createdAt}</p>
      <p>{retired.fileCount.toLocaleString()} files · {retired.byteCount.toLocaleString()} bytes · {retired.reason}</p>
      {retired.status !== "REMOVED" && <p>Removal may already have started. Check the recorded result first. An administrator can explicitly recover this same removal if it was interrupted.</p>}
      <button className="button" disabled={disabled} onClick={() => void inspect(true)}>Load removal actions</button>
      {actions && <ol>{actions.items.map((action) => <li key={action.id} value={action.ordinal}>
        {action.action === "EXECUTE" ? "Removal requested" : "Removal recovery"} · {action.reason} · {action.observation?.outcome === "REMOVED" ? "removed" : action.observation ? "result uncertain" : "awaiting result"}
        {action.observation && (!action.observation.actorCurrent || !action.observation.leaseCurrent) && " · access or execution ownership changed during the request"}
      </li>)}</ol>}
      {actions?.nextCursor && <button className="button" disabled={disabled} onClick={() => void inspect(true, true)}>More removal actions</button>}
    </>}
    {page && !retired && !busy && !uncertain && !error && <PackagingArtifacts materialId={materialId} job={job} />}
    {page && admin && retired?.status !== "REMOVED" && <>
      {!page.enabled ? <p>Local copy removal is disabled by the operator.</p> : <fieldset disabled={frozen}>
        <legend>{retired ? "Recover local copy removal" : "Remove local packaged copy"}</legend>
        <p>This permanently removes only this job's retained local output. Active publication staging must finish or close first. To package again later, create a new packaging job.</p>
        <label>Reason for local copy removal<textarea maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <label><input type="checkbox" checked={ack} onChange={(event) => setAck(event.target.checked)} />I reviewed this package proof and understand that its local files will be permanently removed.</label>
        <button className="button" disabled={frozen || !ack || !reason.trim()} onClick={decide}>{retired ? "Recover this removal" : "Remove this local copy"}</button>
      </fieldset>}
    </>}
  </section>;
}

export function PackagedCopy({ materialId, job }: Props) {
  const session = useSession();
  const boundJob = useMemo(() => ({ id: job.id, proofSha256: job.proofSha256, lastObservationId: job.lastObservationId, requestHash: job.requestHash }),
    [job.id, job.proofSha256, job.lastObservationId, job.requestHash]);
  if (!session || !["ADMIN", "LEADERSHIP"].includes(session.session.user.role)) return null;
  const { id, role } = session.session.user;
  return <CopyControls key={[materialId, boundJob.id, boundJob.proofSha256, boundJob.lastObservationId, boundJob.requestHash, id, role].join(":")}
    materialId={materialId} job={boundJob} actorId={id} admin={role === "ADMIN"} pending={session.pending} />;
}
