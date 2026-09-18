import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/errors";
import { packagingClient, type PackagingJob } from "../api/packagingClient";
import type { PublicationBatch } from "../api/publicationClient";
import { stagingClient as api, stagingStatusLabel, type StagingAction, type StagingCommand, type StagingJob, type StagingPreview, type StagingPreviewRequest, type StagingReservation, type StagingSelection } from "../api/stagingClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ErrorState, LoadingState } from "./PageState";

type Packet = { kind: "reserve"; body: StagingReservation } | { kind: StagingAction; job: StagingJob; body: StagingCommand };
// Keep one actor's uncertain request across in-app navigation, in memory only.
// Returning to Publication never sends it automatically. Reload uses durable history.
let retained: { actorId: string; packet: Packet } | null = null;
const bytes = (value: number) => `${value.toLocaleString()} bytes`;

function PackageChoice({ row, batchId, disabled, selected, onChange }: {
  row: PublicationBatch["items"][number]; batchId: string; disabled: boolean;
  selected?: StagingSelection; onChange: (value: StagingSelection | undefined) => void;
}) {
  const [page, setPage] = useState<Awaited<ReturnType<typeof packagingClient.history>> | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(false);
  const reading = useRef(false), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const load = async (after: string | null = null) => {
    if (reading.current || disabled) return;
    reading.current = true; setBusy(true); setError(false);
    try { const value = await packagingClient.history(row.materialId, after); if (mounted.current) {
      setPage(value); if (value.archived) onChange(undefined);
    } }
    catch { if (mounted.current) setError(true); }
    finally { reading.current = false; if (mounted.current) setBusy(false); }
  };
  const options = page?.archived ? [] : page?.items.filter((job) => job.status === "PACKAGED" && job.batchId === batchId && job.inputHash === row.snapshotHash) ?? [];
  const choose = (job?: PackagingJob) => onChange(job?.lastObservationId && job.proofSha256 ? {
    material_id: row.materialId, execution_id: job.id, expected_observation_id: job.lastObservationId, expected_proof_sha256: job.proofSha256,
  } : undefined);
  return <li className="staging-package"><strong>{row.row.identityName} · {row.row.name}</strong>
    <button className="button" disabled={disabled || busy} onClick={() => void load()}>Load packages for {row.row.identityName}</button>
    {error && <p role="alert">Packages could not be loaded. Retry this material.</p>}
    {page?.archived && <p>This material is archived. It cannot be selected for a new storage upload.</p>}
    {page && <><label>Accepted package for {row.row.identityName}<select value={options.some((job) => job.id === selected?.execution_id) ? selected?.execution_id : ""}
      disabled={disabled || busy || page.archived} onChange={(event) => choose(options.find((job) => job.id === event.target.value))}>
      <option value="">Choose a package</option>{options.map((job) => <option key={job.id} value={job.id}>{new Date(job.createdAt).toLocaleString()} · {job.id}</option>)}
    </select></label>{!options.length && <p>No accepted package for this batch on this history page.</p>}
      {page.nextCursor && <button className="button" disabled={disabled || busy} onClick={() => void load(page.nextCursor)}>Older packages for {row.row.identityName}</button>}</>}
    {selected && <p className="revision-hash">Selected package: {selected.execution_id} · proof {selected.expected_proof_sha256}</p>}
  </li>;
}

function Preparation({ batch, disabled, onPreview }: { batch: PublicationBatch; disabled: boolean; onPreview: (payload: StagingPreviewRequest | null) => void }) {
  const [selected, setSelected] = useState<Record<string, StagingSelection>>({});
  const [offset, setOffset] = useState(0);
  return <fieldset disabled={disabled}><legend>Prepare storage upload</legend>
    <p className="revision-hash">Selected CSV batch: {batch.id}</p>
    <p>Choose one accepted package for each material. The server checks the saved CSV, package proofs and current approvals again.</p>
    <ol start={offset + 1}>{batch.items.slice(offset, offset + 10).map((row) => <PackageChoice key={row.materialId} row={row} batchId={batch.id} disabled={disabled}
      selected={selected[row.materialId]} onChange={(value) => { setSelected((old) => { const next = { ...old }; if (value) next[row.materialId] = value; else delete next[row.materialId]; return next; }); onPreview(null); }} />)}</ol>
    {batch.items.length > 10 && <div className="publication-actions"><button className="button" disabled={offset === 0} onClick={() => setOffset(offset - 10)}>Previous upload materials</button>
      <button className="button" disabled={offset + 10 >= batch.items.length} onClick={() => setOffset(offset + 10)}>Next upload materials</button></div>}
    <p>{Object.keys(selected).length}/{batch.rowCount} packages selected.</p>
    <button className="button" disabled={Object.keys(selected).length !== batch.rowCount} onClick={() => onPreview({ job_id: crypto.randomUUID(),
      expected_snapshot_hash: batch.snapshotHash, expected_csv_sha256: batch.csvSha256, packages: batch.items.map((row) => selected[row.materialId]) })}>Review storage upload</button>
  </fieldset>;
}

function StagingWork({ batch, actorId, admin }: { batch: PublicationBatch | null; actorId: string; admin: boolean }) {
  const initial = retained?.actorId === actorId ? retained.packet : null;
  const packet = useRef<Packet | null>(initial), sending = useRef(false), reading = useRef(false), mounted = useRef(true);
  const load = useCallback(() => api.history(), []), history = useResource(load);
  const [older, setOlder] = useState<Awaited<ReturnType<typeof api.history>> | null>(null);
  const [job, setJob] = useState<StagingJob | null>(initial && initial.kind !== "reserve" ? initial.job : null);
  const [preview, setPreview] = useState<{ value: StagingPreview; body: StagingPreviewRequest } | null>(null);
  const [commands, setCommands] = useState<Awaited<ReturnType<typeof api.dispatches>> | null>(null);
  const [transfers, setTransfers] = useState<{ dispatchId: string; page: Awaited<ReturnType<typeof api.transfers>> } | null>(null);
  const [reason, setReason] = useState(""), [ack, setAck] = useState(false), [abandonAck, setAbandonAck] = useState(false);
  const [busy, setBusy] = useState(false), [readBusy, setReadBusy] = useState(false), [uncertain, setUncertain] = useState(!!initial);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const jobRef = useRef(job), readVersion = useRef(0);
  const adopt = useCallback((value: StagingJob | null) => {
    const previous = jobRef.current;
    if (previous?.id !== value?.id || previous?.status !== value?.status || previous?.lastDispatchId !== value?.lastDispatchId || previous?.lastResultId !== value?.lastResultId) {
      setAck(false); setAbandonAck(false);
    }
    readVersion.current += 1; jobRef.current = value; setJob(value);
  }, []);
  useEffect(() => {
    mounted.current = true;
    const prevent = (event: BeforeUnloadEvent) => { if (packet.current) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", prevent);
    return () => { mounted.current = false; window.removeEventListener("beforeunload", prevent); };
  }, []);
  const jobId = job?.id, polling = job?.status === "RUNNING" || busy;
  useEffect(() => {
    if (!jobId || !polling) return;
    let active = true, pending = false;
    const timer = window.setInterval(() => {
      if (pending) return; pending = true;
      const version = readVersion.current;
      void api.detail(jobId).then((value) => { if (active && version === readVersion.current) adopt(value); }).catch(() => { /* Explicit refresh reports errors. */ }).finally(() => { pending = false; });
    }, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [jobId, polling, adopt]);
  const inspect = async (action: () => Promise<void>) => {
    if (reading.current) return;
    reading.current = true; readVersion.current += 1; setReadBusy(true); setError("");
    try { await action(); }
    catch { if (mounted.current) setError("Storage details could not be verified. Refresh and check your access, source files and package selection."); }
    finally { reading.current = false; if (mounted.current) setReadBusy(false); }
  };
  const clearDecision = () => { setAck(false); setAbandonAck(false); setReason(""); };
  const open = (id: string) => inspect(async () => {
    const value = await api.detail(id);
    if (mounted.current) { adopt(value); setPreview(null); setCommands(null); setTransfers(null); clearDecision(); }
  });
  const review = (body: StagingPreviewRequest | null) => {
    setPreview(null); clearDecision(); setNotice("");
    if (body && batch) {
      adopt(null); setCommands(null); setTransfers(null);
      void inspect(async () => { const value = await api.preview(batch, body); if (mounted.current) setPreview({ value, body }); });
    }
  };
  const send = async () => {
    if (sending.current || reading.current || !packet.current) return;
    const pending = packet.current;
    sending.current = true; readVersion.current += 1; setBusy(true); setError(""); setNotice("");
    const release = () => { if (retained?.packet === pending) retained = null; packet.current = null; };
    try {
      const saved = pending.kind === "reserve" ? await api.reserve(pending.body) : await api.command(pending.job, pending.kind, pending.body);
      release();
      if (mounted.current) { adopt(saved); setUncertain(false); setPreview(null); clearDecision(); setCommands(null); setTransfers(null); setOlder(null); history.retry();
        setNotice(pending.kind === "reserve" ? "Storage job reserved. Review its destination before starting upload." : "Storage job progress updated."); }
    } catch (cause) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        release();
        if (mounted.current) { setUncertain(false); setPreview(null); clearDecision(); history.retry();
          setError(cause.code === "GCS_DISPATCH_BUSY" || cause.code === "GCS_BUSY" ? "Another request is active. Refresh the job before the next action."
            : "The request was rejected. Refresh this job and review current approvals and access. A dispatched request may have stored partial results."); }
      } else if (mounted.current) { setUncertain(true); setError("The outcome is unknown. Recover the same request before sending another action."); }
    } finally { sending.current = false; if (mounted.current) setBusy(false); }
  };
  const submit = (value: Packet) => {
    if (sending.current || reading.current || packet.current) return;
    packet.current = value; retained = { actorId, packet: value }; void send();
  };
  const currentPreview = preview?.value.batchId === batch?.id ? preview : null;
  const reserve = () => {
    if (!currentPreview || !batch || !reason.trim() || !ack) return;
    submit({ kind: "reserve", body: { ...currentPreview.body, batch_id: batch.id, idempotency_key: crypto.randomUUID(),
      expected_plan_sha256: currentPreview.value.planSha256, reason: reason.trim() } });
  };
  const command = (kind: StagingAction) => {
    if (!job || !reason.trim() || !ack || (kind === "abandon" && (!admin || !abandonAck))) return;
    const body: StagingCommand = { idempotency_key: crypto.randomUUID(), expected_plan_sha256: job.planSha256, reason: reason.trim() };
    if (kind !== "close") body.expected_last_dispatch_id = job.lastDispatchId;
    if (kind === "abandon") body.acknowledge_possible_remote_effects = true;
    submit({ kind, job, body });
  };
  const page = older ?? history.data, frozen = busy || uncertain || readBusy;
  const confirmed = !!reason.trim() && ack && !frozen, enabled = history.data?.enabled === true;
  return <section aria-label="Storage upload controls">
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {uncertain && <p>Recover the pending request explicitly. It remains available when you return to this page during this browser session.</p>}
    {uncertain && <button className="button button--primary" disabled={busy || readBusy} onClick={() => void send()}>Recover same storage request</button>}
    {history.error ? <ErrorState message="Storage history could not be loaded." retry={history.retry} /> : !history.data ? <LoadingState label="Loading storage history…" /> : <>
      {!enabled && <p>Storage upload is disabled. You can review, reserve and close unsent jobs. An administrator must configure GCS before transfer.</p>}
      {batch ? <Preparation key={batch.id} batch={batch} disabled={frozen} onPreview={review} /> : <p>Open a saved CSV batch above to prepare a storage job.</p>}
      {currentPreview && <section aria-label="Reviewed storage upload"><h3>Review upload destination</h3>
        <p className="revision-hash">gs://{currentPreview.value.bucketName}/{currentPreview.value.stagingPrefix}/{currentPreview.value.jobId}/</p>
        <p>{currentPreview.value.materials.length} materials · {currentPreview.value.objectCount} files · {bytes(currentPreview.value.totalBytes)}. A completion record is added after all files are verified.</p>
        <p className="revision-hash">Plan SHA-256: {currentPreview.value.planSha256}</p>
        <ul>{currentPreview.value.objects.map((file) => <li key={file.path}><span className="revision-hash">{file.path}</span> · {bytes(file.size)}</li>)}</ul>
        {currentPreview.value.objectCount > currentPreview.value.objects.length && <p>Showing the first {currentPreview.value.objects.length} files.</p>}
        <p>Reserving this plan locks these materials against changes until the storage job is closed.</p>
      </section>}
      <h3>Storage history · all CSV batches</h3>
      {!page?.items.length && <p>No storage jobs.</p>}
      <ul>{page?.items.map((item) => <li key={item.id}><button className="button" disabled={frozen} onClick={() => void open(item.id)}>
        Open {stagingStatusLabel[item.status]} · {new Date(item.createdAt).toLocaleString()}</button><span className="revision-hash">Batch {item.batchId}</span></li>)}</ul>
      {page?.nextCursor && <button className="button" disabled={frozen} onClick={() => void inspect(async () => { const value = await api.history(page.nextCursor); if (mounted.current) setOlder(value); })}>Older storage jobs</button>}
      <button className="button" disabled={busy || readBusy} onClick={() => void inspect(async () => {
        if (job) { const value = await api.detail(job.id); if (mounted.current) adopt(value); }
        if (mounted.current) { setOlder(null); history.retry(); }
      })}>Refresh storage progress</button>
      {job && <section aria-label="Selected storage job"><h3>{stagingStatusLabel[job.status]}</h3>
        <p className="revision-hash">Job: {job.id}</p><p className="revision-hash">CSV batch: {job.batchId}</p>
        <p className="revision-hash">gs://{job.bucketName}/{job.stagingPrefix}/{job.id}/</p>
        <p>{job.materialCount} materials · {job.objectCount} files · {bytes(job.totalBytes)}</p><p>Reason: {job.reason}</p>
        {job.status === "RUNNING" && <p>Progress refreshes automatically. After interruption, check stored files to recover recorded work.</p>}
        {job.status === "RECOVERY_REQUIRED" && <p>Storage evidence is incomplete or current approval checks failed. Checking stored files is read-only. A partial upload may require administrator closure and a new job.</p>}
        {job.status === "STAGED_VERIFIED" && <p>All files and the completion record were verified at the last check. Online import and publication remain separate steps.</p>}
        {job.close && <p>Closed: {job.close.reason}. {job.close.dispatched ? "Closure releases material locks; it does not cancel remote requests or delete stored objects." : "No upload was dispatched."}</p>}
        <button className="button" disabled={readBusy} onClick={() => void inspect(async () => { const value = await api.dispatches(job); if (mounted.current) { setCommands(value); setTransfers(null); } })}>Load storage actions</button>
        {commands && <ol>{commands.items.map((entry) => <li key={entry.id} value={entry.ordinal}>
          {entry.action === "EXECUTE" ? "Upload" : "Read-only check"} · {entry.reason} · {entry.result?.outcome.toLowerCase() ?? "awaiting result"}
          {entry.result && <p>Current inputs: {entry.result.inputsCurrent ? "yes" : "no"} · access: {entry.result.actorCurrent ? "yes" : "no"} · exclusive ownership: {entry.result.leaseCurrent ? "yes" : "no"}</p>}
          <button className="button" disabled={readBusy} onClick={() => void inspect(async () => { const value = await api.transfers(job, entry.id); if (mounted.current) setTransfers({ dispatchId: entry.id, page: value }); })}>Files for action {entry.ordinal}</button>
        </li>)}</ol>}
        {commands?.nextCursor && <button className="button" disabled={readBusy} onClick={() => void inspect(async () => {
          const value = await api.dispatches(job, commands.nextCursor); if (mounted.current) setCommands(value);
        })}>Next storage actions</button>}
        {transfers && <section aria-label="Transfer evidence"><h4>Recorded file transfers</h4><ol>{transfers.page.items.map((file) => <li key={file.id} value={file.ordinal}>
          <span className="revision-hash">{file.path}</span> · {bytes(file.size)} · {file.observation?.outcome.toLowerCase() ?? "awaiting evidence"}
          {file.observation?.receipt && <span> · generation {file.observation.receipt.generation}</span>}
          {file.observation?.failureCode && <span> · {file.observation.failureCode.replaceAll("_", " ").toLowerCase()}</span>}
        </li>)}</ol>{transfers.page.nextCursor && <button className="button" disabled={readBusy} onClick={() => void inspect(async () => {
          const value = await api.transfers(job, transfers.dispatchId, transfers.page.nextCursor); if (mounted.current) setTransfers({ dispatchId: transfers.dispatchId, page: value });
        })}>Next transferred files</button>}</section>}
      </section>}
      {(currentPreview || (job && job.status !== "CLOSED")) && <fieldset disabled={frozen}><legend>Storage decision</legend>
        <label>Reason for storage action<textarea maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <label><input type="checkbox" checked={ack} onChange={(event) => setAck(event.target.checked)} />I reviewed the selected CSV batch, destination and storage progress.</label>
        <div className="publication-actions">
          {currentPreview && <button className="button" disabled={!confirmed} onClick={reserve}>Reserve storage job</button>}
          {job?.status === "RESERVED" && <>
            {enabled && <button className="button button--primary" disabled={!confirmed} onClick={() => command("run")}>Start storage upload</button>}
            <button className="button" disabled={!confirmed} onClick={() => command("close")}>Close unsent storage job</button>
          </>}
          {job?.lastDispatchId && job.status !== "CLOSED" && enabled && <button className="button" disabled={!confirmed} onClick={() => command("reconcile")}>Check stored files</button>}
        </div>
        {admin && job?.lastDispatchId && job.status !== "CLOSED" && <>
          <p>Administrative closure releases material locks. Already sent requests may still finish and objects remain in storage.</p>
          <label><input type="checkbox" checked={abandonAck} onChange={(event) => setAbandonAck(event.target.checked)} />I acknowledge possible late remote effects and retained objects.</label>
          <button className="button" disabled={!confirmed || !abandonAck} onClick={() => command("abandon")}>Close dispatched storage job</button>
        </>}
      </fieldset>}
    </>}
  </section>;
}

export function StagingPanel({ batch }: { batch: PublicationBatch | null }) {
  const user = useSession()?.session.user;
  const [opened, setOpened] = useState(() => !!user && retained?.actorId === user.id);
  const [expanded, setExpanded] = useState(opened);
  if (!user || (user.role !== "ADMIN" && user.role !== "LEADERSHIP")) return null;
  return <article className="panel staging-panel" aria-label="Storage uploads"><details open={expanded} onToggle={(event) => { setExpanded(event.currentTarget.open); if (event.currentTarget.open) setOpened(true); }}>
    <summary>Storage uploads</summary><p>Stage saved CSV and accepted packages in private GCS storage. This does not publish materials or confirm compatibility with the online importer.</p>
    {opened && <StagingWork key={user.id} batch={batch} actorId={user.id} admin={user.role === "ADMIN"} />}
  </details></article>;
}
