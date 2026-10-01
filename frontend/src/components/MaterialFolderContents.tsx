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

export function MaterialFolderContents({ materialId, folderPath, showRefresh = true }: { materialId: string; folderPath: string | null; showRefresh?: boolean }) {
  const [revision, setRevision] = useState(0);
  return <div className="material-file-tree" aria-label="Material folder contents">
    {!folderPath ? <p>No source folder linked.</p> : <><FolderBranch key={revision} materialId={materialId} path="" />
      {showRefresh && <button type="button" className="button" onClick={() => setRevision(value => value + 1)}>Refresh contents</button>}</>}
  </div>;
}

function Directory({ materialId, entry }: { materialId: string; entry: Entry }) {
  const [open, setOpen] = useState(false);
  return <details onToggle={event => { if (event.target === event.currentTarget) setOpen(event.currentTarget.open); }}>
    <summary>{entry.name}</summary>{open && <FolderBranch materialId={materialId} path={entry.path} />}
  </details>;
}

function FolderBranch({ materialId, path }: { materialId: string; path: string }) {
  const load = useCallback(async () => parse(await request(`/materials/${materialId}/folder-contents?path=${encodeURIComponent(path)}`), materialId, path), [materialId, path]);
  const { data, error, cause, retry } = useResource(load);
  const loading = !data && !error;
  return <>
      {loading && <p role="status">Loading folder contents…</p>}
      {error && <p role="alert">{cause instanceof ApiError && cause.status === 404
        ? "The linked folder is unavailable. Check the NAS connection and folder reference."
        : "Folder contents are unavailable. The source connection may be offline; database properties can still be edited."}<button type="button" className="button" onClick={retry}>Retry folder</button></p>}
      {data && <><ul>{data.entries.map((entry) => <li key={entry.path}>{entry.kind === "directory"
        ? <Directory materialId={materialId} entry={entry} /> : <span className="material-file-tree__file">{entry.name}<small>{Math.ceil(entry.size / 1024).toLocaleString()} KB</small></span>}</li>)}</ul>
        {!data.entries.length && <p>This folder is empty.</p>}{data.omitted > 0 && <p>{data.omitted} unsupported entries omitted.</p>}</>}
    </>;
}
