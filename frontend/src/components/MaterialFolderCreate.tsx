import { useCallback, useEffect, useRef, useState } from "react";
import { materialCreationClient, type MaterialFolderCreate as FolderRequest } from "../api/materialCreationClient";
import type { Material } from "../api/materialDto";
import { ApiError } from "../api/errors";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { sessionGeneration } from "../auth/sessionTransport";
import { useNavigationGuard } from "../navigationGuard";

/** A durable folder request for this existing record; never creates another material. */
export function MaterialFolderCreate({ material, disabled, onChanged, onBusyChange }: {
  material: Material; disabled?: boolean; onChanged: () => Promise<boolean>; onBusyChange?: (busy: boolean) => void;
}) {
  const actor = useSession()?.session.user.id ?? "current";
  const storageKey = `reawote.material-folder.${actor}.${material.id}`;
  const [packet, setPacket] = useState<FolderRequest | null>(() => {
    try { const saved = sessionStorage.getItem(storageKey); return saved ? JSON.parse(saved) as FolderRequest : null; } catch { return null; }
  });
  const [template, setTemplate] = useState(packet?.template_name ?? ""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const sending = useRef(false), unknown = useRef(Boolean(packet)), alive = useRef(true);
  const options = useResource(useCallback(() => materialCreationClient.options(), []));
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { onBusyChange?.(busy || packet !== null); return () => onBusyChange?.(false); }, [busy, packet, onBusyChange]);
  useNavigationGuard(() => sending.current || unknown.current);
  function remember(value: FolderRequest | null) {
    setPacket(value);
    try { if (value) sessionStorage.setItem(storageKey, JSON.stringify(value)); else sessionStorage.removeItem(storageKey); } catch { /* In-memory exact retry remains available. */ }
  }
  const create = async () => {
    if (sending.current || (disabled && !packet) || material.isDraft || material.folderPath || (!packet && !options.data)) return;
    const generation = sessionGeneration();
    const live = () => alive.current && generation === sessionGeneration();
    const exact = packet ?? { idempotency_key: crypto.randomUUID(), expected_updated_at: material.updatedAt,
      expected_paths_version: options.data?.pathsVersion ?? null, template_name: template || null };
    const wasUnknown = unknown.current;
    remember(exact); sending.current = true; unknown.current = true; setBusy(true); setError("");
    try {
      const result = await materialCreationClient.createFolder(material.id, exact);
      if (!live()) return;
      if (result.status === "COMPLETED") { remember(null); unknown.current = false; await onChanged(); }
      else setError("Folder creation is unfinished. Recover the same request; the material and its number are retained.");
    } catch (cause) {
      if (!live()) return;
      if (!wasUnknown && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        try {
          await materialCreationClient.lookup(exact.idempotency_key);
        } catch (lookup) {
          if (live() && lookup instanceof ApiError && lookup.status === 404) { remember(null); unknown.current = false; }
        }
      }
      if (live()) setError(unknown.current ? "The folder result is unknown. Recover this same request before leaving." : "The folder could not be created. Check the material, selected template and storage paths, then try again.");
    } finally { sending.current = false; if (live()) setBusy(false); }
  };
  if (material.isDraft) return <p>Complete Customer and Main category to generate a material number and enable its data folder.</p>;
  return <section aria-label="Create material folder">
    <p>Create PREVIEW and SOURCE under this Customer using the existing material number.</p>
    <label>SBS template for new folder<select value={template} disabled={busy || packet !== null || disabled} onChange={event => setTemplate(event.target.value)}>
      <option value="">No template</option>{options.data?.templates.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
    </select></label>
    {options.error && !packet && <p role="status">Folder settings are unavailable. <button type="button" className="button" onClick={options.retry}>Reload folder settings</button></p>}
    {error && <p role="alert" className="field-error">{error}</p>}
    <button type="button" className="button" disabled={busy || (!packet && (disabled || !options.data || options.data.canCreateFolders === false))} onClick={() => void create()}>
      {busy ? "Creating folder…" : packet ? "Recover material folder creation" : "Create material folder"}
    </button>
  </section>;
}
