import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { materialArchiveClient as api, lifecycleRequest, type ArchivePreview, type LifecycleEvent, type LifecycleRequest } from "../api/materialArchiveClient";
import { ApiError } from "../api/errors";
import { useSession } from "../auth/context";
import { useNavigationGuard } from "../navigationGuard";

type Packet = { preview: ArchivePreview; body: LifecycleRequest };
// An uncertain command remains actor-bound and is never automatically resent.
const retained = new Map<string, Packet>();
const listeners = new Set<() => void>();
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
function notify() { listeners.forEach(listener => listener()); }
type Props = { materialId: string; archived?: boolean; label?: string; disabled?: boolean;
  navigate: (path: string) => void; onApplied?: (event: LifecycleEvent) => void; onBusyChange?: (busy: boolean) => void };

function LifecycleCheckbox({ materialId, archived = false, label = "Archived", disabled, actorId, navigate, onApplied, onBusyChange }: Props & { actorId: string }) {
  const packet = useSyncExternalStore(subscribe, () => retained.get(actorId) ?? null);
  const [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(Boolean(packet)), [error, setError] = useState("");
  const [savedState, setSavedState] = useState(archived);
  const mounted = useRef(true), sending = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useNavigationGuard(() => sending.current || retained.has(actorId));
  useEffect(() => {
    const prevent = (event: BeforeUnloadEvent) => {
      if (sending.current || retained.has(actorId)) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [actorId]);
  const send = async (current: Packet, readOnly = false, wasUnknown = false) => {
    if (sending.current || current.preview.id !== materialId) return;
    sending.current = true; setBusy(true); setError(""); onBusyChange?.(true);
    const release = () => { if (retained.get(actorId) === current) { retained.delete(actorId); notify(); } };
    try {
      const saved = await (readOnly ? api.recover : api.command)(current.preview, actorId, current.body);
      release();
      if (mounted.current) { setSavedState(saved.action === "ARCHIVE"); setUncertain(false); onApplied?.(saved); }
    } catch (cause) {
      if (!wasUnknown && !readOnly && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        release();
        if (mounted.current) { setUncertain(false); setError("The action was rejected. Refresh the material before trying again."); }
      } else if (mounted.current) {
        setUncertain(true);
        setError(readOnly && cause instanceof ApiError && cause.status === 404
          ? "No saved result is visible yet. The original request may still commit. Check again or retry the same request."
          : "The outcome could not be verified. Check the saved result or retry the same request.");
      }
    } finally {
      sending.current = false;
      if (mounted.current) { setBusy(false); onBusyChange?.(retained.has(actorId)); }
    }
  };
  const toggle = async () => {
    if (sending.current || retained.has(actorId) || disabled) return;
    sending.current = true; setBusy(true); setError(""); onBusyChange?.(true);
    let current: Packet | null = null;
    try {
      const preview = await api.preview(materialId, savedState ? "RESTORE" : "ARCHIVE");
      if (!mounted.current) return;
      if (!preview.canApply) {
        setError(preview.blockedCode === "MATERIAL_LIFECYCLE_EXTERNAL_STATE_BLOCKED"
          ? "A recorded storage upload must be reconciled before changing Archived. Closing that job does not prove that external files were removed."
          : `Archived could not be changed: ${preview.blockedCode?.replaceAll("_", " ").toLowerCase()}.`);
      } else if (!retained.has(actorId)) {
        current = { preview, body: lifecycleRequest(preview) };
        retained.set(actorId, current); notify(); setUncertain(false);
      }
    } catch { if (mounted.current) setError("The current material could not be verified. Refresh it before trying again."); }
    finally { sending.current = false; if (mounted.current) { setBusy(false); if (!current) onBusyChange?.(false); } }
    if (current && mounted.current) await send(current);
  };
  const ownPending = packet?.preview.id === materialId;
  return <div className="material-archive-control">
    <label><input type="checkbox" aria-label={label} checked={savedState} disabled={disabled || busy || Boolean(packet)} onChange={() => void toggle()} />{label === "Archived" && "Archived"}</label>
    {busy && <span role="status">Saving…</span>}
    {error && <p role="alert">{error}</p>}
    {ownPending && uncertain && <div className="publication-actions">
      <button className="button" disabled={busy} onClick={() => void send(packet, true, true)}>Check saved lifecycle result</button>
      <button className="button" disabled={busy} onClick={() => void send(packet, false, true)}>Retry exact lifecycle request</button>
    </div>}
    {packet && !ownPending && <button className="button" onClick={() => navigate(`/material-archives/${packet.preview.id}`)}>Open pending material</button>}
  </div>;
}

export function MaterialLifecyclePanel(props: Props) {
  const auth = useSession();
  if (auth?.session.user.role !== "ADMIN") return <input type="checkbox" aria-label={props.label ?? "Archived"} checked={props.archived ?? false} disabled />;
  return <LifecycleCheckbox key={`${auth.session.user.id}:${props.materialId}:${props.archived}`} {...props} actorId={auth.session.user.id} />;
}
