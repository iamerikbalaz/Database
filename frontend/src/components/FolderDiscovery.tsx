import { useEffect, useRef, useState, type FormEvent } from "react";
import { discoveryClient, discoveryError, discoveryPath, type FolderDiscovery as Listing } from "../api/discoveryClient";
import { useSession } from "../auth/context";

export function FolderDiscovery({ materialId, identity, disabled, onSelect }: {
  materialId: string; identity: string; disabled: boolean; onSelect: (path: string) => void;
}) {
  const role = useSession()?.session.user.role;
  const [open, setOpen] = useState(false), [parent, setParent] = useState("");
  const [listing, setListing] = useState<Listing>(), [error, setError] = useState("");
  const [pending, setPending] = useState(false), [filter, setFilter] = useState(""), [page, setPage] = useState(0);
  const active = useRef(true), requestInFlight = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  if (role !== "ADMIN") return null;

  const browse = async (path: string) => {
    if (disabled || requestInFlight.current) return;
    setListing(undefined); setError(""); setFilter(""); setPage(0);
    try { discoveryPath(path); }
    catch { setError("Use a relative path with forward slashes, or leave it empty for the source root."); return; }
    requestInFlight.current = true; setPending(true); setParent(path);
    try {
      const result = await discoveryClient.listing(materialId, identity, path);
      if (active.current) setListing(result);
    } catch (cause) { if (active.current) setError(discoveryError(cause)); }
    finally { requestInFlight.current = false; if (active.current) setPending(false); }
  };
  const submit = (event: FormEvent) => { event.preventDefault(); void browse(parent); };
  const matching = listing?.directories.filter((item) => item.name.toLocaleLowerCase().includes(filter.toLocaleLowerCase())) ?? [];
  const busy = disabled || pending;
  return <section className="folder-discovery" aria-label="Browse source folders">
    <button type="button" className="button" disabled={busy} aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Close source browser" : "Browse source folders"}</button>
    {open && <>
      <p>Browse one level at a time. Choose a folder with the exact identity, then check and link it below.</p>
      <form aria-label="Browse parent folder" onSubmit={submit}>
        <label className="form-field">Parent relative path
          <input value={parent} disabled={busy} placeholder="Empty = source root" onChange={(event) => { setParent(event.target.value); setListing(undefined); setError(""); }} />
        </label>
        <div className="button-row">
          <button type="submit" className="button" disabled={busy}>{pending ? "Loading folders…" : "List folders"}</button>
          <button type="button" className="button" disabled={busy || !parent} onClick={() => void browse("")}>Source root</button>
          <button type="button" className="button" disabled={busy || !listing?.parentPath} onClick={() => void browse(listing!.parentPath.split("/").slice(0, -1).join("/"))}>Parent folder</button>
        </div>
      </form>
      {error && <p role="alert" className="field-error">{error}</p>}
      {listing && <>
        <p role="status">{listing.directories.length} folders in {listing.parentPath || "source root"}.</p>
        {listing.omittedEntries > 0 && <p className="muted">{listing.omittedEntries} files or entries that cannot be browsed were omitted.</p>}
        <label className="form-field">Filter these folders<input value={filter} disabled={busy} onChange={(event) => { setFilter(event.target.value); setPage(0); }} /></label>
        {matching.length === 0 && <p>No matching folders in this level.</p>}
        <ul className="folder-discovery-list">
          {matching.slice(page * 50, (page + 1) * 50).map((item) => <li key={item.path}>
            <span>{item.name}{item.identityMatches && <strong> · Exact identity match</strong>}</span>
            <div className="button-row">
              <button type="button" className="button" disabled={busy} aria-label={`Open folder ${item.name}`} onClick={() => void browse(item.path)}>Open</button>
              {item.identityMatches && <button type="button" className="button" disabled={busy} onClick={() => { onSelect(item.path); setOpen(false); }}>Use this folder</button>}
            </div>
          </li>)}
        </ul>
        {matching.length > 50 && <div className="button-row">
          <button type="button" className="button" disabled={busy || page === 0} onClick={() => setPage(page - 1)}>Previous folders</button>
          <span>Page {page + 1} of {Math.ceil(matching.length / 50)}</span>
          <button type="button" className="button" disabled={busy || (page + 1) * 50 >= matching.length} onClick={() => setPage(page + 1)}>Next folders</button>
        </div>}
      </>}
    </>}
  </section>;
}
