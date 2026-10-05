import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { materialLocalClient, type AutomaticFileCheckResult } from "../api/materialLocalClient";
import type { Material } from "../api/materialDto";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { MaterialFolderContents } from "./MaterialFolderContents";
import { MaterialNameDialog } from "./MaterialNameDialog";
import { FileCheckProgress } from "./FileCheckProgress";
import { useFileCheckProgress } from "./useFileCheckProgress";
import { sessionGeneration } from "../auth/sessionTransport";
import { useNavigationGuard } from "../navigationGuard";
import { FileCheckJobUnavailableError } from "../api/materialCheckJobs";
import { Icon } from "./Icon";
import { MaterialFolderCreate } from "./MaterialFolderCreate";

export function MaterialDataFolder({ material, onChanged, disabled, onBusyChange }: { material: Material; onChanged: () => Promise<boolean>; disabled?: boolean; onBusyChange?: (busy: boolean) => void }) {
  const role = useSession()?.session.user.role;
  const load = useCallback(() => materialLocalClient.info(material.id), [material.id]);
  const result = useResource(load);
  const [error, setError] = useState(""), [pending, setPending] = useState(false), sending = useRef(false);
  const [destination, setDestination] = useState<{ parent: string; absolutePath: string } | null>(null);
  const [contentsRevision, setContentsRevision] = useState(0);
  useEffect(() => { onBusyChange?.(pending || destination !== null); return () => onBusyChange?.(false); }, [pending, destination, onBusyChange]);
  const canMove = (role === "ADMIN" || role === "PRODUCTION_LEAD") && !material.isArchived && result.data?.canMove;
  const run = async (action: "open" | "choose") => {
    if (sending.current || disabled) return;
    sending.current = true; setPending(true); setError("");
    try {
      if (action === "open") await materialLocalClient.openFolder(material.id);
      else {
        const chosen = await materialLocalClient.destination(material.id);
        if (chosen.absolutePath !== null && chosen.parent !== null) setDestination({ absolutePath: chosen.absolutePath, parent: chosen.parent });
      }
    } catch { setError(action === "open" ? "The folder could not be opened. Check the local desktop connection." : "A destination could not be selected. Check the local desktop connection and choose a folder within the permitted test library."); }
    finally { sending.current = false; setPending(false); }
  };
  return <article className="panel panel--wide material-data-folder"><div className="material-section-heading"><h2>Material data folder</h2>
    <button type="button" className="button material-icon-button" aria-label="Refresh contents" title="Refresh contents" disabled={disabled || pending || destination !== null || !material.folderPath}
      onClick={() => { setContentsRevision(value => value + 1); result.retry(); }}><Icon name="refresh" size={18} /></button></div>
    <MaterialFolderContents key={contentsRevision} materialId={material.id} folderPath={material.folderPath} showRefresh={false} />
    <label>Absolute folder path<input className="folder-absolute-path" readOnly value={result.data?.absolutePath ?? ""} placeholder={result.error ? "Folder path unavailable" : !result.data ? "Loading folder path…" : "No local folder linked"} /></label>
    {result.error && <p role="status">The local folder connection is unavailable. <button type="button" className="button" onClick={result.retry}>Reload folder connection</button></p>}
    {error && <p role="alert" className="field-error">{error}</p>}
    <div className="form-actions"><button type="button" className="button" disabled={disabled || pending || !result.data?.canOpen} onClick={() => void run("open")}>Open folder</button>
      {canMove && <button type="button" className="button" disabled={disabled || pending} onClick={() => void run("choose")}>Choose destination folder…</button>}</div>
    {destination && <MaterialNameDialog material={material} destination={destination} onClose={() => setDestination(null)} onChanged={onChanged} />}
    {!material.folderPath && !material.isArchived && (role === "ADMIN" || role === "PRODUCTION_LEAD") && <MaterialFolderCreate material={material} disabled={disabled || pending || destination !== null} onChanged={onChanged} onBusyChange={onBusyChange} />}
  </article>;
}

export function MaterialDataCheck({ materialId, updatedAt, disabled, onChanged, onBusyChange, actionTarget, dockOnly = false }: {
  materialId: string; updatedAt?: string; disabled?: boolean; onChanged?: () => Promise<boolean>; onBusyChange?: (busy: boolean) => void;
  actionTarget?: HTMLElement | null; dockOnly?: boolean;
}) {
  const [result, setResult] = useState<AutomaticFileCheckResult | null>(null);
  const [pending, setPending] = useState(false), [error, setError] = useState(""), sending = useRef(false);
  const checkProgress = useFileCheckProgress(), mounted = useRef(true), currentId = useRef(materialId);
  useNavigationGuard(() => sending.current);
  useEffect(() => { mounted.current = true; currentId.current = materialId; return () => { mounted.current = false; }; }, [materialId]);
  useEffect(() => { onBusyChange?.(pending); return () => onBusyChange?.(false); }, [pending, onBusyChange]);
  const check = async () => {
    if (sending.current || disabled) return;
    const generation = sessionGeneration();
    const live = () => mounted.current && currentId.current === materialId && generation === sessionGeneration();
    sending.current = true; setPending(true); setError(""); setResult(null);
    try { const checked = await materialLocalClient.check(materialId, { ...checkProgress.begin(), expectedUpdatedAt: updatedAt }); if (live()) { setResult(checked); await onChanged?.(); } }
    catch (cause) { if (live()) setError(cause instanceof FileCheckJobUnavailableError ? cause.message : "The material data check could not finish. Check the folder connection and try again."); }
    finally { sending.current = false; checkProgress.finish(); if (live()) setPending(false); }
  };
  const action = <button type="button" className="button button--primary" disabled={disabled || pending} onClick={() => void check()}>{pending ? "Checking material data…" : "Check material data"}</button>;
  return <>{actionTarget ? createPortal(action, actionTarget) : dockOnly ? null : action}
    {(pending || error || result) && <article className="panel panel--wide material-data-check" aria-label="Automatic file check report"><h2>Automatic file check</h2>
    {pending && <FileCheckProgress progress={checkProgress.progress} resume={checkProgress.resume} total={1} />}
    {error && <p role="alert" className="field-error">{error}</p>}
    {result && <p role="status"><span className={`automatic-file-check automatic-file-check--${result.status.toLowerCase()}`}>{result.status === "NOT_CHECKED" ? "not checked" : result.status === "OK" ? "OK" : "issues"}</span>{result.complete ? " · Full file check completed." : " · Only a preliminary inspection completed. Run the full check before relying on this result."}</p>}
    {result && <label className="material-check-report">Issues and report<textarea aria-label="Issues and report" readOnly rows={12} value={`Issues\n${result.issues.length ? result.issues.map(issue => `• ${issue}`).join("\n") : "No issues found by the current checks."}\n\n${result.report}`} /></label>}
  </article>}</>;
}
