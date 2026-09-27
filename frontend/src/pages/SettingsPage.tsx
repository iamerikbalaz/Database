import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/errors";
import { packagingSettingsClient as api, type PackagingSettings, type PackagingSettingsUpdate } from "../api/packagingSettingsClient";
import { useSession } from "../auth/context";
import { NavigationLink } from "../components/NavigationLink";
import { useNavigationGuard } from "../navigationGuard";

export function SettingsPage({ navigate }: { navigate: (path: string) => void }) {
  const user = useSession()?.session.user;
  return <SettingsWork key={user?.id ?? "signed-out"} admin={user?.role === "ADMIN"} navigate={navigate} />;
}

function SettingsWork({ admin, navigate }: { admin: boolean; navigate: (path: string) => void }) {
  const [settings, setSettings] = useState<PackagingSettings | null>(null);
  const [cutoff, setCutoff] = useState(""), [timezone, setTimezone] = useState("");
  const [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const pending = useRef<PackagingSettingsUpdate | null>(null), sending = useRef(false), alive = useRef(false);
  const readVersion = useRef(0);
  useNavigationGuard(() => pending.current !== null);
  const apply = (value: PackagingSettings) => { setSettings(value); setCutoff(value.cutoffDate); setTimezone(value.storageTimezone); };
  useEffect(() => {
    alive.current = true;
    const version = ++readVersion.current;
    let current = true;
    void api.current().then(value => { if (current && readVersion.current === version) apply(value); }).catch(() => { if (current && readVersion.current === version) setError("Settings could not be loaded."); });
    return () => { current = false; alive.current = false; };
  }, []);
  const refresh = async () => {
    if (sending.current || pending.current) return;
    sending.current = true; setBusy(true); setError("");
    const version = ++readVersion.current;
    try { const value = await api.current(); if (alive.current && readVersion.current === version) apply(value); }
    catch { if (alive.current && readVersion.current === version) setError("Settings could not be loaded."); }
    finally { sending.current = false; if (alive.current) setBusy(false); }
  };
  const send = async () => {
    if (sending.current || !pending.current || !admin) return;
    sending.current = true; setBusy(true); setError(""); setNotice("");
    readVersion.current += 1;
    try {
      await api.save(pending.current);
      pending.current = null;
      if (!alive.current) return;
      setUncertain(false); setNotice("Packaging settings saved. Future batches use this rule.");
      try { const current = await api.current(); if (alive.current) apply(current); }
      catch { if (alive.current) { setSettings(null); setError("Saved successfully. Refresh to load the current settings."); } }
    } catch (cause) {
      if (!uncertain && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        pending.current = null;
        if (alive.current) setError(cause.status === 409 ? "Settings changed. Refresh before saving again." : "Settings were rejected. Check the date, timezone and your access.");
      } else if (alive.current) { setUncertain(true); setError("The save result is unknown. Recover the same request before leaving."); }
    } finally { sending.current = false; if (alive.current) setBusy(false); }
  };
  const save = () => {
    if (sending.current || pending.current || !settings || !admin || !cutoff || !timezone.trim()) return;
    pending.current = { idempotency_key: crypto.randomUUID(), expected_version: settings.version, cutoff_date: cutoff, storage_timezone: timezone.trim() };
    void send();
  };
  return <section><div className="page-heading"><div><p className="eyebrow">Workspace</p><h1>Settings</h1></div></div>
    {error && <p className="form-error" role="alert">{error}</p>}{notice && <p className="success-notice" role="status">{notice}</p>}
    {uncertain && <button className="button" disabled={busy} onClick={() => void send()}>Recover same settings request</button>}
    <article className="panel"><h2>Automatic ZIP packaging</h2>
      <p>The original master folder date selects method A before the cutoff and method B on or after it.</p>
      <dl><dt>Method A</dt><dd>Normalize ZIP dates to 1 January 2026.</dd><dt>Method B</dt><dd>Retain packaging dates.</dd></dl>
      <p>Changes apply to future batches. Saved batches, jobs and files retain their recorded settings.</p>
      {settings ? <fieldset disabled={!admin || busy || uncertain}><legend>Global date rule</legend>
        <label>Cutoff date<input type="date" min="1980-01-01" max="2100-12-31" value={cutoff} onChange={event => setCutoff(event.target.value)} /></label>
        <label>Timezone<input value={timezone} maxLength={100} onChange={event => setTimezone(event.target.value)} placeholder="Europe/Prague" /></label>
        <p>The boundary is midnight in this timezone.</p>
        {admin && <button className="button button--primary" disabled={!cutoff || !timezone.trim()} onClick={save}>Save packaging settings</button>}
      </fieldset> : !error && <p role="status">Loading packaging settings…</p>}
      {!admin && <p>An administrator can change these settings.</p>}
      <button className="button" disabled={busy || uncertain} onClick={() => void refresh()}>Refresh settings</button>
    </article>
    {admin && <article className="panel"><h2>Accounts</h2><NavigationLink href="/settings/users" navigate={navigate}>Manage users</NavigationLink></article>}
  </section>;
}
