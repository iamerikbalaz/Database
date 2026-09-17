import { useRef, useState } from "react";
import { aiServiceClient, type AiCredential } from "../api/aiServiceClient";
import { ApiError } from "../api/errors";
import { useSession } from "../auth/context";

type Action = { kind: "issue"; send: () => ReturnType<typeof aiServiceClient.issue> } | { kind: "revoke"; send: () => ReturnType<typeof aiServiceClient.revoke> };

function CredentialManager({ materialId }: { materialId: string }) {
  const [history, setHistory] = useState<Awaited<ReturnType<typeof aiServiceClient.history>> | null>(null);
  const [issued, setIssued] = useState<Awaited<ReturnType<typeof aiServiceClient.issue>> | null>(null);
  const [lifetime, setLifetime] = useState("900"), [reason, setReason] = useState("");
  const [pending, setPending] = useState(false), [loading, setLoading] = useState(false), [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const sending = useRef(false), reading = useRef(false), action = useRef<Action | null>(null);
  const frozen = pending || loading || uncertain;
  const load = async (after: string | null = null) => {
    if (reading.current || sending.current || uncertain) return;
    reading.current = true; setLoading(true); setError("");
    try { setHistory(await aiServiceClient.history(materialId, after)); }
    catch { setError("Service access history could not be loaded. Retry loading it."); }
    finally { reading.current = false; setLoading(false); }
  };
  const send = async (build?: () => Action) => {
    if (sending.current || reading.current) return;
    action.current ??= build?.() ?? null;
    if (!action.current) return;
    sending.current = true; setPending(true); setError(""); setNotice("");
    try {
      const current = action.current;
      if (current.kind === "issue") {
        const result = await current.send(); setIssued(result);
        setNotice(result.token ? "Access issued. Transfer the one-time credential to your intended client, then hide it."
          : "This request already issued access. Its secret cannot be recovered; revoke this credential and issue another.");
      } else {
        const result = await current.send();
        setIssued((previous) => previous?.credential.id === result.id ? { credential: result, token: null } : previous);
        setHistory((previous) => previous ? { ...previous, items: previous.items.map((item) => item.id === result.id ? result : item) } : previous);
        setNotice("Service access revoked. Its proposal history remains available for human review.");
      }
      action.current = null; setUncertain(false); setReason("");
    } catch (cause) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        action.current = null; setUncertain(false);
        setError(cause.code === "AI_SERVICE_ACTIVE_LIMIT" ? "Ten credentials are still within their validity period. Revoke an unused credential or wait for expiry."
          : "Service access could not be changed. Check your administrator access and reload its history.");
      } else { setUncertain(true); setError("The outcome is unknown. Retry the same request before changing service access."); }
    } finally { sending.current = false; setPending(false); }
  };
  const revoke = (item: AiCredential) => void send(() => {
    const payload = { idempotency_key: crypto.randomUUID(), reason: reason.trim() };
    return { kind: "revoke", send: () => aiServiceClient.revoke(materialId, item.id, payload) };
  });
  const copy = async () => {
    if (!issued?.token) return;
    try {
      await navigator.clipboard.writeText(issued.token);
      setNotice("Credential copied. Transfer it to your intended client, then clear your clipboard.");
    } catch { setError("Clipboard access was unavailable. Select and copy the credential field manually."); }
  };
  const row = (item: AiCredential) => <>
    <code>{item.id}</code><p>Expires <time dateTime={item.expiresAt}>{item.expiresAt}</time>{item.revokedAt ? " · Revoked" : " · Not explicitly revoked"}</p>
    <button className="button" type="button" disabled={frozen || !reason.trim() || !!item.revokedAt} onClick={() => revoke(item)}>Revoke credential {item.id}</button>
  </>;
  return <details><summary>Manage AI service access</summary>
    <p>A service can read this material's limited context and submit proposals. Access ends when its lifetime or your issuing session expires, when you log out, or when revoked. Human review and approval remain required.</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <fieldset disabled={frozen}><legend>Service access controls</legend>
      <label>Reason for service access change<textarea maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <form onSubmit={(event) => {
        event.preventDefault(); void send(() => {
          const payload = { idempotency_key: crypto.randomUUID(), lifetime_seconds: Number(lifetime), reason: reason.trim() };
          return { kind: "issue", send: () => aiServiceClient.issue(materialId, payload) };
        });
      }}>
        <label>Service access lifetime<select value={lifetime} onChange={(event) => setLifetime(event.target.value)}><option value="900">15 minutes</option><option value="1800">30 minutes</option><option value="3600">1 hour</option></select></label>
        <button className="button" disabled={!reason.trim() || issued !== null}>Issue one-time service access</button>
      </form>
    </fieldset>
    {issued && <section aria-label="Last issued service credential"><h3>Last issued credential</h3>{row(issued.credential)}
      {issued.token && <label>One-time service credential<input type="password" readOnly autoComplete="off" spellCheck={false} value={issued.token} /></label>}
      {issued.token && <button className="button" type="button" disabled={frozen} onClick={() => void copy()}>Copy one-time credential</button>}
      <p>The credential is kept only in this page's memory. After hiding or leaving this page, its secret cannot be retrieved. Existing proposals remain available after revocation.</p>
      <button className="button" type="button" disabled={frozen} onClick={() => { setIssued(null); setNotice(""); }}>Hide one-time credential and clear this result</button>
    </section>}
    {uncertain && <button className="button" disabled={pending} onClick={() => void send()}>Retry same service access request</button>}
    <button className="button" disabled={frozen} onClick={() => void load()}>Load service access history</button>
    {history && <ul>{history.items.filter((item) => item.id !== issued?.credential.id).map((item) => <li key={item.id}>{row(item)}</li>)}</ul>}
    {history?.nextCursor && <button className="button" disabled={frozen} onClick={() => void load(history.nextCursor)}>Older service credentials</button>}
  </details>;
}

export function AiServicePanel({ materialId }: { materialId: string }) {
  const role = useSession()?.session.user.role;
  return role === "ADMIN" ? <article className="panel catalog-content ai-content" aria-label="AI service access"><CredentialManager key={materialId} materialId={materialId} /></article> : null;
}
