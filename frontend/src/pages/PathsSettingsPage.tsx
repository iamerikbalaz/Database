import { useCallback, useEffect, useRef, useState } from "react";
import { pathSettingsClient, type PathSettings, type PathSettingsUpdate } from "../api/pathSettingsClient";
import { ApiError } from "../api/errors";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { sessionGeneration } from "../auth/sessionTransport";
import { NavigationLink } from "../components/NavigationLink";
import { ErrorState, LoadingState } from "../components/PageState";
import { useNavigationGuard } from "../navigationGuard";

export function PathsSettingsPage({ navigate }: { navigate: (path: string) => void }) {
  const admin = useSession()?.session.user.role === "ADMIN";
  const resource = useResource(useCallback(() => pathSettingsClient.current(), []));
  if (!admin) return <section><h1>Paths</h1><p>Only an administrator can configure storage locations.</p></section>;
  return <section><NavigationLink className="back-link" href="/settings" navigate={navigate}>Back to settings</NavigationLink><div className="page-heading"><div><p className="eyebrow">Settings</p><h1>Paths</h1><p>Shared folders used for new orders and materials.</p></div></div>
    {resource.error ? <ErrorState message="Path settings could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading paths…" /> : <PathsEditor key={resource.data.version} initial={resource.data} refresh={resource.retry} />}
  </section>;
}
function PathsEditor({ initial, refresh }: { initial: PathSettings; refresh: () => void }) {
  const [settings, setSettings] = useState(initial), [sbs, setSbs] = useState(initial.sbsTemplatesRoot), [orders, setOrders] = useState(initial.ordersRoot), [materials, setMaterials] = useState(initial.materialsRoot);
  const [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const packet = useRef<{ generation: number; payload: PathSettingsUpdate } | null>(null), sending = useRef(false), alive = useRef(true);
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
      setUncertain(false); setSettings(result); setSbs(result.sbsTemplatesRoot); setOrders(result.ordersRoot); setMaterials(result.materialsRoot); setNotice("Paths saved. Existing folders have not been moved.");
    } catch (cause) {
      if (!alive.current) return;
      if (!wasUncertain && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) { packet.current = null; setError(cause.message); }
      else { setUncertain(true); setError("The save result is unknown. Recover the same request before leaving."); }
    } finally { sending.current = false; if (alive.current) setBusy(false); }
  };
  return <article className="panel record-form"><h2>Storage folders</h2>
    <p>Choose existing absolute folders. These settings affect new data; changing them does not move or relink existing records.</p>
    <form onSubmit={event => { event.preventDefault(); if (busy || uncertain || packet.current) return; packet.current = { generation: sessionGeneration(), payload: { idempotency_key: crypto.randomUUID(), expected_version: settings.version, sbs_templates_root: sbs.trim(), orders_root: orders.trim(), materials_root: materials.trim() } }; void save(); }}>
      <fieldset disabled={busy || uncertain}><legend>Paths</legend><div className="directory-fields">
        <label className="directory-field--wide">SBS templates folder<input required value={sbs} maxLength={2048} onChange={event => setSbs(event.target.value)} /></label>
        <label className="directory-field--wide">Orders root<input required value={orders} maxLength={2048} onChange={event => setOrders(event.target.value)} /></label>
        <label className="directory-field--wide">Customers and materials root<input required value={materials} maxLength={2048} onChange={event => setMaterials(event.target.value)} /></label>
      </div><p>During testing, material data stays inside Test_data. A changed material root must also be selected by the local file service before creating materials.</p><button className="button button--primary" disabled={!sbs.trim() || !orders.trim() || !materials.trim()}>Save paths</button></fieldset>
    </form>
    {error && <p className="form-error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {uncertain ? <button className="button" disabled={busy} onClick={() => void save()}>Recover same paths request</button> : error && <button className="button" disabled={busy} onClick={refresh}>Reload paths</button>}
  </article>;
}
