import type { DirectorySync as Sync } from "../api/directoryClient";
export function DirectorySync({ sync, notionPageId, detailed = false }: { sync: Sync; notionPageId: string | null; detailed?: boolean }) {
  return <span className="directory-sync">{sync?.enabled === false ? <span>Notion sync is disabled{detailed && <small>Changes are saved in the app. Configure the integration to send them to Notion.</small>}</span> : sync ? <span title={sync.error ?? undefined}>{sync.state.replaceAll("_", " ").toLowerCase()}{sync.error && <small role="status">{sync.error}</small>}</span> : notionPageId ? "Linked" : "Pending"}
    {notionPageId && <a href={`https://www.notion.so/${notionPageId.replaceAll("-", "")}`} target="_blank" rel="noreferrer">Open in Notion</a>}</span>;
}
