import { useCallback, useState } from "react";
import { request } from "../api/client";
import { record, uuid } from "../api/dto";
import { ApiError } from "../api/errors";
import { useResource } from "../api/useResource";

interface Entry { name: string; path: string; kind: "directory" | "file"; size: number; }
function parse(value: unknown, id: string, path: string) {
  const data = record(value);
  if (uuid(data.material_id) !== id || data.path !== path || !Array.isArray(data.entries) || data.entries.length > 4096 ||
      typeof data.omitted_entries !== "number" || !Number.isSafeInteger(data.omitted_entries) || data.omitted_entries < 0 || data.entries.length + data.omitted_entries > 4096) throw new Error("Invalid folder contents");
  const names = new Set<string>();
  const entries: Entry[] = data.entries.map((value) => {
    const entry = record(value);
    if (typeof entry.name !== "string" || !entry.name || entry.name === "." || entry.name === ".." || entry.name.length > 255 ||
        /[\p{C}/\\:]/u.test(entry.name) || names.has(entry.name) || entry.path !== (path ? path + "/" : "") + entry.name ||
        !["file", "directory"].includes(String(entry.kind)) || typeof entry.size !== "number" || !Number.isSafeInteger(entry.size) || entry.size < 0 || entry.size > 64 * 1024 ** 3 || (entry.kind === "directory" && entry.size !== 0)) throw new Error("Invalid folder entry");
    names.add(entry.name);
    return entry as unknown as Entry;
  });
  return { entries, omitted: data.omitted_entries };
}

export function MaterialFolderContents({ materialId, folderPath }: { materialId: string; folderPath: string | null }) {
  const [open, setOpen] = useState(false);
  return <details className="panel" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Folder contents</summary>
    {open && (!folderPath ? <p>No source folder linked.</p> : <FolderBrowser materialId={materialId} folderPath={folderPath} />)}
  </details>;
}

function FolderBrowser({ materialId, folderPath }: { materialId: string; folderPath: string }) {
  const [path, setPath] = useState("");
  const load = useCallback(async () => parse(await request(`/materials/${materialId}/folder-contents?path=${encodeURIComponent(path)}`), materialId, path), [materialId, path]);
  const { data, error, cause, retry } = useResource(load);
  const loading = !data && !error;
  return <>
      <p className="muted">{folderPath}{path && "/" + path}</p>
      <div className="button-row"><button type="button" className="button" disabled={loading || !path} onClick={() => setPath(path.split("/").slice(0, -1).join("/"))}>Parent folder</button>
        <button type="button" className="button" disabled={loading} onClick={retry}>Refresh contents</button></div>
      {loading && <p role="status">Loading folder contents…</p>}
      {error && <p role="alert">{cause instanceof ApiError && cause.status === 404
        ? "The linked folder is unavailable. Check the NAS connection and folder reference."
        : "Folder contents are unavailable. The source connection may be offline; database properties can still be edited."}</p>}
      {data && <><div className="table-scroll"><table aria-label="Material folder contents"><thead><tr><th>Name</th><th>Type</th><th>Size</th></tr></thead>
        <tbody>{data.entries.map((entry) => <tr key={entry.path}><td>{entry.kind === "directory"
          ? <button type="button" className="button" onClick={() => setPath(entry.path)}>{entry.name}</button> : entry.name}</td>
          <td>{entry.kind === "directory" ? "Folder" : "File"}</td><td>{entry.kind === "file" ? `${Math.ceil(entry.size / 1024).toLocaleString()} KB` : "—"}</td></tr>)}</tbody></table></div>
        {!data.entries.length && <p>This folder is empty.</p>}{data.omitted > 0 && <p>{data.omitted} unsupported entries omitted.</p>}</>}
    </>;
}
