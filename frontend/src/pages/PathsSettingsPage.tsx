import { useCallback, useEffect, useRef, useState } from "react";
import { pathSettingsClient, type PathField, type PathSettings, type PathSettingsUpdate } from "../api/pathSettingsClient";
import { ApiError } from "../api/errors";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { sessionGeneration } from "../auth/sessionTransport";
import { NavigationLink } from "../components/NavigationLink";
import { ErrorState, LoadingState } from "../components/PageState";
import { useNavigationGuard } from "../navigationGuard";
import "./PathsSettingsPage.css";

export function PathsSettingsPage({ navigate }: { navigate: (path: string) => void }) {
  const admin = useSession()?.session.user.role === "ADMIN";
  const resource = useResource(useCallback(() => pathSettingsClient.current(), []));
  if (!admin) return <section><h1>Paths</h1><p>Only an administrator can configure storage locations.</p></section>;
  return <section><NavigationLink className="back-link" href="/settings" navigate={navigate}>Back to settings</NavigationLink><div className="page-heading"><div><p className="eyebrow">Settings</p><h1>Paths</h1><p>Shared folders used for new orders and materials.</p></div></div>
    {resource.error ? <ErrorState message="Path settings could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading paths…" /> : <PathsEditor key={resource.data.version} initial={resource.data} refresh={resource.retry} />}
  </section>;
}
function PathsEditor({ initial, refresh }: { initial: PathSettings; refresh: () => void }) {
  const [settings, setSettings] = useState(initial), [sbs, setSbs] = useState(initial.sbsTemplatesRoot), [orders, setOrders] = useState(initial.ordersRoot), [materials, setMaterials] = useState(initial.materialsRoot), [published, setPublished] = useState(initial.publishedLibraryRoot);
  const [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [picking, setPicking] = useState<PathField | null>(null);
  const packet = useRef<{ generation: number; payload: PathSettingsUpdate } | null>(null), sending = useRef(false), alive = useRef(true), pickerOpen = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useNavigationGuard(() => packet.current !== null);
  const save = async () => {
    if (sending.current || packet.current?.generation !== sessionGeneration()) return;
    const current = packet.current; if (!current) return;
    const wasUncertain = uncertain;
    sending.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const result = await pathSettingsClient.save(current.payload);
      packet.current = null;
      if (!alive.current || current.generation !== sessionGeneration()) return;
      setUncertain(false); setSettings(result); setSbs(result.sbsTemplatesRoot); setOrders(result.ordersRoot); setMaterials(result.materialsRoot); setPublished(result.publishedLibraryRoot); setNotice("Paths saved. Existing folders have not been moved.");
    } catch (cause) {
      if (!alive.current) return;
      if (!wasUncertain && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) { packet.current = null; setError(cause.message); }
      else { setUncertain(true); setError("The save result is unknown. Recover the same request before leaving."); }
    } finally { sending.current = false; if (alive.current) setBusy(false); }
  };
  const choose = async (field: PathField, update: (value: string) => void) => {
    if (pickerOpen.current || sending.current || packet.current || !settings.canSelectFolder) return;
    const generation = sessionGeneration();
    pickerOpen.current = true; setPicking(field); setError(""); setNotice("");
    try {
      const selected = await pathSettingsClient.selectFolder(field);
      if (!alive.current || generation !== sessionGeneration()) return;
      if (selected !== null) { update(selected); setNotice("Folder selected. Save paths to apply this change."); }
      else setNotice("Folder selection cancelled. Paths have not changed.");
    } catch {
      if (alive.current && generation === sessionGeneration()) setError("The folder picker is unavailable or the selected folder is not allowed. You can enter an existing absolute path manually.");
    } finally { pickerOpen.current = false; if (alive.current) setPicking(null); }
  };
  const fields: { key: PathField; label: string; value: string; update: (value: string) => void }[] = [
    { key: "sbs_templates_root", label: "SBS templates folder", value: sbs, update: setSbs },
    { key: "orders_root", label: "Orders root", value: orders, update: setOrders },
    { key: "materials_root", label: "Customers and materials root", value: materials, update: setMaterials },
    { key: "published_library_root", label: "Published materials library", value: published, update: setPublished },
  ];
  return <article className="panel record-form"><h2>Storage folders</h2>
    <p>Choose absolute folders. Templates, orders and material folders must exist; the published library can be configured while its drive is disconnected. Changing paths does not move or relink existing records.</p>
    <form onSubmit={event => { event.preventDefault(); if (busy || uncertain || packet.current || pickerOpen.current) return; packet.current = { generation: sessionGeneration(), payload: { idempotency_key: crypto.randomUUID(), expected_version: settings.version, sbs_templates_root: sbs.trim(), orders_root: orders.trim(), materials_root: materials.trim(), published_library_root: published.trim() } }; void save(); }}>
      <fieldset disabled={busy || uncertain || picking !== null}><legend>Paths</legend><div className="directory-fields">
        {fields.map(field => <div className="directory-field--wide paths-folder-field" key={field.key}>
          <label htmlFor={`path-${field.key}`}>{field.label}</label>
          <div className="paths-folder-control"><input id={`path-${field.key}`} required value={field.value} maxLength={2048} onChange={event => field.update(event.target.value)} />
            <button className="button" type="button" disabled={!settings.canSelectFolder} aria-label={`Choose folder for ${field.label}`} onClick={() => void choose(field.key, field.update)}>{picking === field.key ? "Choosing…" : "Choose folder"}</button>
          </div>
        </div>)}
      </div>
        {!settings.canSelectFolder && <p>System folder selection is available on the local desktop. You can enter paths manually here.</p>}
        <p>During testing, material data stays inside Test_data. A changed material root must also be selected by the local file service before creating materials.</p>
        <p>The published library path is reserved for material snapshots in customer/material folders, excluding SOURCE. Saving this path does not copy any files.</p>
        <button className="button button--primary" disabled={fields.some(field => !field.value.trim())}>Save paths</button></fieldset>
    </form>
    {error && <p className="form-error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {uncertain ? <button className="button" disabled={busy} onClick={() => void save()}>Recover same paths request</button> : error && <button className="button" disabled={busy || picking !== null} onClick={refresh}>Reload paths</button>}
  </article>;
}
