import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../api/errors";
import { HISTORY_PAGE_SIZE } from "../api/historyPage";
import { useSession } from "../auth/context";

interface Props<T extends { id: string }> {
  scope: string;
  label: string;
  initial?: T[];
  load: (after: string | null) => Promise<T[]>;
  children: (items: T[]) => ReactNode;
}

// History pages never replace the parent's current review or active operation.
// Remount on actor/target/latest-record changes to retire any in-flight result.
export function HistoryPages<T extends { id: string }>(props: Props<T>) {
  const actor = useSession()?.session.user.id ?? "anonymous";
  const key = `${actor}:${props.scope}:${props.initial?.[0]?.id ?? "unloaded"}:${props.initial?.length ?? 0}`;
  return <HistoryPage key={key} {...props} />;
}

function HistoryPage<T extends { id: string }>({ label, initial, load, children }: Props<T>) {
  const [page, setPage] = useState<{ items: T[]; older: boolean } | null>(null);
  const [busy, setBusy] = useState(false), [failed, setFailed] = useState(false), [denied, setDenied] = useState(false);
  const mounted = useRef(false), sending = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const items = denied ? undefined : page?.items ?? initial;
  async function read(after: string | null) {
    if (sending.current) return;
    sending.current = true; setBusy(true); setFailed(false);
    try {
      const result = await load(after);
      if (mounted.current) { setPage({ items: result, older: after !== null }); setDenied(false); }
    } catch (error) {
      if (mounted.current) {
        setFailed(true);
        if (error instanceof ApiError && [401, 403, 404].includes(error.status)) { setPage(null); setDenied(true); }
      }
    } finally {
      if (mounted.current) { sending.current = false; setBusy(false); }
    }
  }
  return <div aria-busy={busy}>
    <button type="button" className="button" disabled={busy} onClick={() => void read(null)}>{items ? "Latest" : "Load"} {label}</button>
    {items && items.length === HISTORY_PAGE_SIZE && <button type="button" className="button" disabled={busy} onClick={() => void read(items[items.length - 1].id)}>Older {label}</button>}
    {failed && <p role="alert">History could not be loaded. Retry or reload the latest records.</p>}
    {items && <>
      <p>{page?.older ? "Older records" : "Latest records"} · {items.length} on this page.</p>
      {items.length === 0 ? <p>{page?.older ? "No older records." : "No recorded history yet."}</p> : children(items)}
      {items.length > 0 && items.length < HISTORY_PAGE_SIZE && <p>No older records.</p>}
    </>}
  </div>;
}
