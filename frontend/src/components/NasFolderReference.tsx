import { useState } from "react";

/** Browsers cannot reliably open SMB paths; keep the exact NAS path copyable. */
export function NasFolderReference({ path }: { path?: string | null }) {
  const [notice, setNotice] = useState("");
  if (!path) return <>No folder linked</>;
  return <div className="resource-folder"><code>{path}</code><button className="button" onClick={() => { if (!navigator.clipboard?.writeText) { setNotice("Select and copy the path above."); return; } void navigator.clipboard.writeText(path).then(() => setNotice("Path copied"), () => setNotice("Select and copy the path above.")); }}>Copy folder path</button>{notice && <small role="status">{notice}</small>}</div>;
}
