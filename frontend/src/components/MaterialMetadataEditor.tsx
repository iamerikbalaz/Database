import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/errors";
import type { Material } from "../api/materialDto";
import { metadataClient, type MetadataObservation, type MetadataOperation, type MetadataSave } from "../api/metadataClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { useNavigationGuard } from "../navigationGuard";
import { ErrorState, LoadingState } from "./PageState";

interface Props { material: Material; onChanged?: () => Promise<boolean>; onBusyChange?: (busy: boolean) => void; }
export function MaterialMetadataEditor(props: Props) {
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const actor = useSession()?.session.user.id;
  const load = useCallback(() => { void actor; void props.material.updatedAt; return metadataClient.inspect(props.material.id); }, [props.material.id, props.material.updatedAt, actor]);
  const source = useResource(load);
  return <article className="panel" aria-label="Source metadata">
    <h2>Metadata</h2>
    <p>Color and sample size are saved to <code>metadata.txt</code> in the material folder.</p>
    {message && <p role={message.error ? "alert" : "status"}>{message.text}</p>}
    {source.error ? <ErrorState message="Source metadata could not be loaded." retry={source.retry} /> : !source.data ? <LoadingState label="Loading source metadata…" /> :
      <MetadataForm key={`${props.material.id}:${source.data.expectedUpdatedAt}:${source.data.sha256}`} {...props} source={source.data} reload={source.retry} report={setMessage} />}
  </article>;
}

function MetadataForm({ material, source, onChanged, onBusyChange, reload, report }: Props & { source: MetadataObservation; reload: () => void; report: (message: { text: string; error: boolean } | null) => void }) {
  const role = useSession()?.session.user.role;
  const allowed = role === "ADMIN" || role === "PRODUCTION_LEAD" || role === "PROCESSOR";
  const [color, setColor] = useState(source.values.hex_color ?? "");
  const [width, setWidth] = useState(source.values.width_cm ?? "");
  const [height, setHeight] = useState(source.values.height_cm ?? "");
  const [pending, setPending] = useState(false), sending = useRef(false);
  const [active, setActive] = useState<MetadataOperation | null>(source.active);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const exactRequest = useRef<MetadataSave | null>(null);
  const busy = pending || uncertain || active?.status === "RUNNING";
  useEffect(() => { onBusyChange?.(busy); return () => onBusyChange?.(false); }, [busy, onBusyChange]);
  useNavigationGuard(() => sending.current || exactRequest.current !== null || active?.status === "RUNNING");
  const editable = allowed && source.available && source.writesEnabled && source.editable && !busy;
  const validDimension = (value: string) => value === "" || /^(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,4})?$/.test(value) && Number(value) > 0;
  const run = async (recover = false) => {
    if (sending.current) return;
    if (!recover && ((!/^#?[0-9A-Fa-f]{6}$/.test(color) && color !== "") || !validDimension(width) || !validDimension(height))) {
      setError("Enter a six-digit HEX color and positive sizes with up to four decimal places, or leave a value blank."); return;
    }
    sending.current = true; setPending(true); setError(""); report(null);
    try {
      let result: MetadataOperation;
      if (active) result = await metadataClient.resume(material.id, active.id);
      else {
        exactRequest.current ??= { idempotency_key: crypto.randomUUID(), expected_updated_at: source.expectedUpdatedAt,
          expected_sha256: source.sha256, values: { hex_color: color || null, width_cm: width || null, height_cm: height || null } };
        result = await metadataClient.save(material.id, exactRequest.current);
      }
      if (result.status === "RUNNING") { setActive(result); setUncertain(true); return; }
      exactRequest.current = null; setActive(null); setUncertain(false);
      if (result.status === "COMPLETED") {
        report({ text: "Metadata saved to the material folder.", error: false });
        if (onChanged) await onChanged().catch(() => false);
      } else report({ text: "Source metadata changed or could not be edited safely. Review the reloaded values before saving.", error: true });
      reload();
    } catch (cause) {
      if (!active && cause instanceof ApiError && (cause.status >= 400 && cause.status < 500 || cause.code === "SOURCE_MUTATIONS_DISABLED" || cause.code === "SOURCE_METADATA_UNAVAILABLE")) {
        exactRequest.current = null; setUncertain(false);
        setError(cause.code === "SOURCE_MUTATIONS_DISABLED" ? "Source writes are disabled. Reload the source status." : cause.code === "SOURCE_METADATA_UNAVAILABLE" ? "The source is unavailable. No save was started." : "The material changed or editing is no longer allowed. Reload before saving.");
      } else {
        setUncertain(true);
        setError("The save outcome is unknown. Recover the same save before making another change.");
      }
    } finally { sending.current = false; setPending(false); }
  };
  return <>
    {!source.available && <p role="status">{material.folderPath ? "Source unavailable. Showing the last recorded values." : "Link the material folder to edit its metadata."}</p>}
    {source.available && !source.writesEnabled && <p role="status">Read-only: source writes are disabled.</p>}
    {source.available && !source.editable && <p role="status">This metadata format cannot be edited safely.</p>}
    {source.sourceStatus === "MISSING" && <p>{Object.values(source.values).some((value) => value !== null) ? "Showing recorded values. " : ""}Saving creates the missing root metadata.txt.</p>}
    {error && <p role="alert" className="field-error">{error}</p>}
    <form onSubmit={(event) => { event.preventDefault(); void run(); }}>
      <div className="form-grid">
        <label>Color HEX<input aria-label="Color HEX" value={color} maxLength={7} placeholder="#AABBCC" disabled={!editable} onChange={(event) => setColor(event.target.value)} /></label>
        <label>Sample width (cm)<input aria-label="Sample width (cm)" inputMode="decimal" value={width} disabled={!editable} onChange={(event) => setWidth(event.target.value)} /></label>
        <label>Sample height (cm)<input aria-label="Sample height (cm)" inputMode="decimal" value={height} disabled={!editable} onChange={(event) => setHeight(event.target.value)} /></label>
      </div>
      {allowed && <div className="form-actions">
        <button className="button" type="submit" disabled={!editable}>Save metadata</button>
        {!busy && <button className="button button--secondary" type="button" onClick={reload}>Reload source</button>}
      </div>}
    </form>
    {(uncertain || active) && <div role="status"><p>The save is pending verification. Its recorded request will be reused.</p>
      {allowed && <button className="button" disabled={pending} onClick={() => void run(true)}>Recover metadata save</button>}</div>}
  </>;
}
