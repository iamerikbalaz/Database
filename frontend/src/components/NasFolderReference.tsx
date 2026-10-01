import { useState } from "react";
import { Icon } from "./Icon";

/** Browsers cannot reliably open SMB paths; keep the exact NAS path copyable. */
export function NasFolderReference({ path }: { path?: string | null }) {
  const [notice, setNotice] = useState("");
  if (!path) return <>No folder linked</>;
  return <div className="resource-folder"><code title={path}>{path}</code><button type="button" className="button folder-copy-button" aria-label="Copy folder path" title="Copy folder path" onClick={() => { if (!navigator.clipboard?.writeText) { setNotice("Select and copy the path above."); return; } void navigator.clipboard.writeText(path).then(() => setNotice("Path copied"), () => setNotice("Select and copy the path above.")); }}><Icon name="copy" size={16} /></button>{notice && <small role="status">{notice}</small>}</div>;
}
