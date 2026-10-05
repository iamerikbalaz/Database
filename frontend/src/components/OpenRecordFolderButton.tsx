import { useEffect, useRef, useState } from "react";
import { materialLocalClient } from "../api/materialLocalClient";
import { directoryClient } from "../api/directoryClient";
import { sessionGeneration } from "../auth/sessionTransport";
import { Icon } from "./Icon";
import "./OpenRecordFolderButton.css";

export function OpenRecordFolderButton({ kind, id, name, folderPath, disabled = false }: {
  kind: "material" | "order"; id: string; name: string; folderPath: string | null; disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState(false);
  const alive = useRef(false), sending = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const open = async () => {
    if (sending.current || disabled || !folderPath) return;
    sending.current = true; setBusy(true); setError(false);
    const generation = sessionGeneration();
    try { await (kind === "material" ? materialLocalClient.openFolder(id) : directoryClient.openFolder(id)); }
    catch { if (alive.current && generation === sessionGeneration()) setError(true); }
    finally { sending.current = false; if (alive.current) setBusy(false); }
  };
  return <span className="record-folder-action">
    <button type="button" className="button open-record-folder" disabled={disabled || busy || !folderPath}
      aria-label={`Open folder for ${name}`} title={!folderPath ? "No data folder assigned" : busy ? "Opening folder…" : "Open data folder"}
      onClick={event => { event.stopPropagation(); void open(); }}><Icon name="folder" size={16} /></button>
    {error && <span className="record-folder-error" role="alert">Folder could not be opened. Check that the drive is connected and try again.</span>}
  </span>;
}
