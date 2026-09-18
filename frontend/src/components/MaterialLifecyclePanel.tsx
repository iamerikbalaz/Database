import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { materialArchiveClient as api, lifecycleRequest, type ArchivePreview, type LifecycleEvent, type LifecycleRequest } from "../api/materialArchiveClient";
import { ApiError } from "../api/errors";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ErrorState, LoadingState } from "./PageState";

type Packet = { preview: ArchivePreview; body: LifecycleRequest };
// One unresolved request per actor, in memory only. Navigation never resends it.
const retained = new Map<string, Packet>();
type Props = { materialId: string; archived?: boolean; navigate: (path: string) => void; onApplied?: (event: LifecycleEvent) => void };

function LifecycleWork({ materialId, archived = false, actorId, navigate, onApplied }: Props & { actorId: string }) {
  const initial = retained.get(actorId) ?? null;
  const packet = useRef<Packet | null>(initial), [pending, setPending] = useState(initial);
  const [preview, setPreview] = useState<ArchivePreview | null>(null), [explanation, setExplanation] = useState(""), [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(!!initial), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const load = useCallback(() => api.history(materialId, cursor), [materialId, cursor]), history = useResource(load);
  const mounted = useRef(true), sending = useRef(false);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const review = async () => {
    if (sending.current || packet.current) return;
    sending.current = true; setBusy(true); setPreview(null); setExplanation(""); setAck(false); setError(""); setNotice("");
    try {
      const value = await api.preview(materialId, archived ? "RESTORE" : "ARCHIVE");
      if (mounted.current) setPreview(value);
    } catch { if (mounted.current) setError("The current material and its eligibility could not be verified. Reload the record before trying again."); }
    finally { sending.current = false; if (mounted.current) setBusy(false); }
  };
  const send = async (readOnly = false) => {
    const current = packet.current;
    if (!current || current.preview.id !== materialId || sending.current) return;
    sending.current = true; setBusy(true); setError("");
    const wasUnknown = uncertain;
    const release = () => { if (retained.get(actorId) === current) retained.delete(actorId); packet.current = null; };
    try {
      const saved = await (readOnly ? api.recover : api.command)(current.preview, actorId, current.body);
      release();
      if (mounted.current) {
        setPending(null); setUncertain(false); setPreview(null); setExplanation(""); setAck(false);
        setNotice(`Recorded ${saved.action === "ARCHIVE" ? "archive" : "restore"} action ${saved.version}. Refresh the record to see its current state.`);
        history.retry(); onApplied?.(saved);
      }
    } catch (cause) {
      if (!wasUnknown && !readOnly && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        release();
        if (mounted.current) { setPending(null); setPreview(null); setExplanation(""); setAck(false);
          setError("The action was rejected. Reload the material and review its current eligibility before starting a new request."); }
      } else if (mounted.current) {
        setUncertain(true);
        setError(readOnly && cause instanceof ApiError && cause.status === 404
          ? "No saved result is visible yet. The original request may still commit. Keep this request and check again or retry it exactly."
          : "The outcome could not be verified. Check the saved result or retry this exact request before starting another lifecycle change.");
      }
    } finally { sending.current = false; if (mounted.current) setBusy(false); }
  };
  const confirm = () => {
    if (!preview || !ack || sending.current || packet.current) return;
    try {
      const current = { preview, body: lifecycleRequest(preview, explanation) };
      retained.set(actorId, current); packet.current = current; setPending(current); setUncertain(false); void send();
    } catch { setError("Enter a readable reason of at most 2000 characters and review the current eligibility."); }
  };
  const ownPending = pending?.preview.id === materialId;
  return <div>
    <p>Archive removes the material from active work. Its identity, original files, saved packages and history are preserved.</p>
    <p>Restoring makes the record available again. Inventory, metadata and approvals must be checked again before publication.</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {pending && !ownPending && <p>An unresolved lifecycle request belongs to another material. <button className="button" onClick={() => navigate(`/material-archives/${pending.preview.id}`)}>Open pending material</button></p>}
    {ownPending && <div className="pending-command"><h3>Pending {pending.body.action === "ARCHIVE" ? "archive" : "restore"}</h3>
      <p>{pending.preview.name} · {pending.preview.technicalIdentity}</p><p>{pending.body.reason}</p>
      <p className="revision-hash">Request: {pending.body.request_key}</p>
      {uncertain && <div className="publication-actions"><button className="button" disabled={busy} onClick={() => void send(true)}>Check saved lifecycle result</button>
        <button className="button" disabled={busy} onClick={() => void send()}>Retry exact lifecycle request</button></div>}
    </div>}
    {!pending && <><button type="button" className="button" disabled={busy} onClick={() => void review()}>{archived ? "Review restore" : "Review archive"}</button>
      {preview && <fieldset disabled={busy}><legend>{preview.action === "ARCHIVE" ? "Archive review" : "Restore review"}</legend>
        <p>{preview.name} · {preview.technicalIdentity}</p>
        {!preview.canApply ? <p role="status">{preview.blockedCode === "MATERIAL_LIFECYCLE_EXTERNAL_STATE_BLOCKED"
          ? "This material has a recorded storage upload attempt. Closing that job does not prove that external files were removed. Archive is unavailable until external state can be reconciled."
          : `This action is currently blocked: ${preview.blockedCode?.replaceAll("_", " ").toLowerCase()}. Resolve active work or publication state and review again.`}</p> : <>
          <label>Reason for lifecycle change<textarea maxLength={2000} value={explanation} onChange={(event) => setExplanation(event.target.value)} /></label>
          <label><input type="checkbox" checked={ack} onChange={(event) => setAck(event.target.checked)} />I reviewed this material and understand that current metadata and approvals will be cleared.</label>
          <button type="button" className="button" disabled={!ack || !explanation.trim() || busy} onClick={confirm}>{preview.action === "ARCHIVE" ? "Confirm archive" : "Confirm restore"}</button>
        </>}
      </fieldset>}
    </>}
    <h3>Archive and restore history</h3>
    {history.error ? <ErrorState message="Lifecycle history could not be verified." retry={history.retry} /> : !history.data ? <LoadingState label="Loading lifecycle history…" /> : <>
      {!history.data.items.length && <p>No archive or restore actions recorded.</p>}
      <ol className="company-history-list">{history.data.items.map((item) => <li key={item.id}><details>
        <summary>{item.action === "ARCHIVE" ? "Archived" : "Restored"} · {new Date(item.createdAt).toLocaleString()}</summary>
        <p>{item.reason}</p><p className="revision-hash">Actor: {item.actorId}</p><p>Recorded action {item.version}. Later changes may have changed the current state.</p>
      </details></li>)}</ol>
      <div className="publication-actions"><button className="button" onClick={() => { if (cursor) setCursor(null); else history.retry(); }}>Latest lifecycle actions</button>
        <button className="button" disabled={!history.data.nextCursor} onClick={() => setCursor(history.data!.nextCursor)}>Older lifecycle actions</button></div>
    </>}
  </div>;
}

export function MaterialLifecyclePanel(props: Props) {
  const auth = useSession(), [open, setOpen] = useState(false);
  const actorId = auth?.session.user.id;
  useLayoutEffect(() => {
    const prevent = (event: BeforeUnloadEvent) => {
      if (actorId && retained.has(actorId)) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [actorId]);
  if (auth?.session.user.role !== "ADMIN") return null;
  return <details className="panel panel--wide" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Archive and restore</summary>
    {open && <LifecycleWork key={`${auth.session.user.id}:${props.materialId}`} {...props} actorId={auth.session.user.id} />}
  </details>;
}
