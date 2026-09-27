import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/errors";
import { technicalClient } from "../api/technicalClient";
import { useSession } from "../auth/context";
import { useNavigationGuard } from "../navigationGuard";

type Props = { materialIds: string[]; disabled: boolean; onBusyChange: (busy: boolean) => void; onChecked: () => Promise<void> };
type Queue = { ids: string[]; index: number; packet: { id: string; generation: number; key: string } | null };

export function PublicationSourceChecks(props: Props) {
  const actor = useSession()?.session.user.id;
  return <SourceChecks key={actor ?? "anonymous"} {...props} />;
}

function SourceChecks({ materialIds, disabled, onBusyChange, onChecked }: Props) {
  const queue = useRef<Queue | null>(null), sending = useRef(false), alive = useRef(false), wasUncertain = useRef(false);
  const [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false), [message, setMessage] = useState("");
  useNavigationGuard(() => queue.current !== null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { onBusyChange(busy || uncertain); return () => onBusyChange(false); }, [busy, uncertain, onBusyChange]);
  const send = async () => {
    if (sending.current || !queue.current || disabled) return;
    sending.current = true; setBusy(true); setMessage("");
    try {
      const work = queue.current;
      while (work.index < work.ids.length && alive.current) {
        if (!work.packet) {
          const id = work.ids[work.index], current = await technicalClient.current(id);
          if (!alive.current) return;
          work.packet = { id, generation: current.review.generation, key: crypto.randomUUID() };
        }
        const packet = work.packet;
        const result = await technicalClient.prepare(packet.id, packet.generation, packet.key);
        work.packet = null; wasUncertain.current = false;
        if (!alive.current) return;
        setUncertain(false);
        if (!result.validation?.canApprove || result.validation.errors.length) {
          queue.current = null;
          const findings = result.validation?.errors.map(item => `${item.code.replaceAll("_", " ").toLowerCase()}${item.path ? ` (${item.path})` : ""}`).join("; ");
          setMessage(`Source check failed for material ${packet.id}. ${findings || result.review.failureCode?.replaceAll("_", " ").toLowerCase() || "No valid technical report was returned."}`);
          return;
        }
        work.index += 1;
        setMessage(`Checked ${work.index} of ${work.ids.length} materials.`);
      }
      queue.current = null;
      if (alive.current) { await onChecked(); if (alive.current) setMessage(""); }
    } catch (cause) {
      if (!queue.current?.packet || (!wasUncertain.current && cause instanceof ApiError && cause.status >= 400 && cause.status < 500)) {
        queue.current = null;
        if (alive.current) setMessage("Source checks stopped. Check the material, source connection and your access before trying again.");
      } else {
        wasUncertain.current = true;
        if (alive.current) { setUncertain(true); setMessage("A source check result is unknown. Recover the same request to continue this selection."); }
      }
    } finally { sending.current = false; if (alive.current) setBusy(false); }
  };
  const start = () => {
    if (disabled || sending.current || queue.current || !materialIds.length || materialIds.length > 100) return;
    queue.current = { ids: [...materialIds], index: 0, packet: null };
    void send();
  };
  return <section className="panel" aria-label="Publication source checks">
    <h2>Check source files</h2><p>Checks image and metadata integrity for this selection, then opens the export preview. Human Checked remains a separate property.</p>
    {message && <p role={uncertain ? "alert" : "status"}>{message}</p>}
    {uncertain ? <button className="button" disabled={busy || disabled} onClick={() => void send()}>Recover same source check</button>
      : <button className="button button--primary" disabled={busy || disabled || !materialIds.length || materialIds.length > 100} onClick={start}>Check sources and review</button>}
  </section>;
}
