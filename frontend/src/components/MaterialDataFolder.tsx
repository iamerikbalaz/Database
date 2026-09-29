import { useCallback, useRef, useState } from "react";
import { materialLocalClient, type AutomaticFileCheckResult } from "../api/materialLocalClient";
import type { Material } from "../api/materialDto";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { MaterialFolderContents } from "./MaterialFolderContents";
import { MaterialNameDialog } from "./MaterialNameDialog";

export function MaterialDataFolder({ material, onChanged, disabled }: { material: Material; onChanged: () => Promise<boolean>; disabled?: boolean }) {
  const role = useSession()?.session.user.role;
  const load = useCallback(() => materialLocalClient.info(material.id), [material.id]);
  const result = useResource(load);
  const [error, setError] = useState(""), [pending, setPending] = useState(false), sending = useRef(false);
  const [destination, setDestination] = useState<{ parent: string; absolutePath: string } | null>(null);
  const canMove = (role === "ADMIN" || role === "PRODUCTION_LEAD") && !material.isArchived && result.data?.canMove;
  const run = async (action: "open" | "choose") => {
    if (sending.current) return;
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
  return <article className="panel panel--wide material-data-folder"><h2>Material data folder</h2>
    <MaterialFolderContents materialId={material.id} folderPath={material.folderPath} />
    <label>Absolute folder path<input className="folder-absolute-path" readOnly value={result.data?.absolutePath ?? ""} placeholder={result.error ? "Folder path unavailable" : !result.data ? "Loading folder path…" : "No local folder linked"} /></label>
    {result.error && <p role="status">The local folder connection is unavailable. <button type="button" className="button" onClick={result.retry}>Reload folder connection</button></p>}
    {error && <p role="alert" className="field-error">{error}</p>}
    <div className="form-actions"><button type="button" className="button" disabled={disabled || pending || !result.data?.canOpen} onClick={() => void run("open")}>Open folder</button>
      {canMove && <button type="button" className="button" disabled={disabled || pending} onClick={() => void run("choose")}>Choose destination folder…</button>}</div>
    {destination && <MaterialNameDialog material={material} destination={destination} onClose={() => setDestination(null)} onChanged={onChanged} />}
  </article>;
}

export function MaterialDataCheck({ materialId, disabled, onChanged }: { materialId: string; disabled?: boolean; onChanged?: () => Promise<boolean> }) {
  const [result, setResult] = useState<AutomaticFileCheckResult | null>(null);
  const [pending, setPending] = useState(false), [error, setError] = useState(""), sending = useRef(false);
  const check = async () => {
    if (sending.current) return;
    sending.current = true; setPending(true); setError(""); setResult(null);
    try { setResult(await materialLocalClient.check(materialId)); await onChanged?.(); }
    catch { setError("The material data check could not finish. Check the folder connection and try again."); }
    finally { sending.current = false; setPending(false); }
  };
  return <article className="panel panel--wide"><h2>Automatic file check</h2>
    <p>Checks map names, required COL / ROUGH / NRM maps, image formats and bit depth, matching master resolution, metadata and PNG previews at 1200 × 1200 pixels. Human Checked remains a separate review.</p>
    <button className="button button--primary" disabled={disabled || pending} onClick={() => void check()}>{pending ? "Checking material data…" : "Check material data"}</button>
    {pending && <p role="status">Reading and checking source images. Large materials may take several minutes.</p>}
    {error && <p role="alert" className="field-error">{error}</p>}
    {result && <p role="status"><span className={`automatic-file-check automatic-file-check--${result.status.toLowerCase()}`}>{result.status === "NOT_CHECKED" ? "not checked" : result.status === "OK" ? "OK" : "issues"}</span>{result.complete ? " · Full file check completed." : " · Only a preliminary inspection completed. Run the full check before relying on this result."}</p>}
    {result && <label className="material-check-report">Issues and report<textarea aria-label="Issues and report" readOnly rows={12} value={`Issues\n${result.issues.length ? result.issues.map(issue => `• ${issue}`).join("\n") : "No issues found by the current checks."}\n\n${result.report}`} /></label>}
  </article>;
}
