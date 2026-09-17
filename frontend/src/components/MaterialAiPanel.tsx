import { useRef, useState } from "react";
import { aiContentClient, type AiDraft, type PublishingContext, type ContentSource } from "../api/aiContentClient";
import { catalogClient, type MaterialContent } from "../api/catalogClient";
import { ApiError } from "../api/errors";
import { useSession } from "../auth/context";

type Snapshot = { context: PublishingContext; content: MaterialContent; sources: ContentSource[]; history: Awaited<ReturnType<typeof aiContentClient.drafts>> };
type Operation = { send: () => Promise<unknown>; kind: "source" | "proposal" | "adoption" };

function tagsFromLines(value: string) {
  const tags = value.split(/\r?\n/).map((tag) => tag.trim()).filter(Boolean);
  if (tags.length > 100 || tags.some((tag) => tag.length > 100 || tag.includes(":"))) throw new Error("Use at most 100 individual tags without colons, up to 100 characters each.");
  return tags;
}

function Workspace({ snapshot, materialId, reload, onAdopted, onSourcesChanged }: {
  snapshot: Snapshot; materialId: string; reload: () => void; onAdopted: () => void; onSourcesChanged: () => void;
}) {
  const role = useSession()?.session.user.role;
  const editor = role === "ADMIN" || role === "PRODUCTION_LEAD" || role === "PROCESSOR";
  const [url, setUrl] = useState(""); const [sourceReason, setSourceReason] = useState("");
  const [provider, setProvider] = useState(""); const [model, setModel] = useState(""); const [prompt, setPrompt] = useState("");
  const [description, setDescription] = useState(""); const [tags, setTags] = useState("");
  const [sourceIds, setSourceIds] = useState<string[]>([]); const [proposalReason, setProposalReason] = useState("");
  const [selected, setSelected] = useState<AiDraft | null>(null);
  const [reviewedDescription, setReviewedDescription] = useState(""); const [reviewedTags, setReviewedTags] = useState("");
  const [adoptionReason, setAdoptionReason] = useState(""); const [confirmed, setConfirmed] = useState(false);
  const [history, setHistory] = useState(snapshot.history); const [paging, setPaging] = useState(false);
  const [pending, setPending] = useState(false); const [uncertain, setUncertain] = useState(false); const [error, setError] = useState("");
  const operation = useRef<Operation | null>(null); const sending = useRef(false); const pageSending = useRef(false);
  const frozen = pending || uncertain || paging;

  const send = async (build?: () => Operation) => {
    if (sending.current || pageSending.current) return;
    try { operation.current ??= build?.() ?? null; }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Check the proposal values."); return; }
    if (!operation.current) return;
    sending.current = true; setPending(true); setError("");
    try {
      const current = operation.current; await current.send(); operation.current = null; setUncertain(false);
      if (current.kind === "adoption") onAdopted();
      else { if (current.kind === "source") onSourcesChanged(); reload(); }
    } catch (cause) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        operation.current = null; setUncertain(false);
        setError("The request was rejected. Check your access and inputs. If the material or sources changed, reload before preparing a new request.");
      } else {
        setUncertain(true); setError("The outcome is unknown. Retry the same request before making more changes.");
      }
    } finally { sending.current = false; setPending(false); }
  };
  const approve = () => void send(() => {
    const payload = { idempotency_key: crypto.randomUUID(), url: url.trim(), reason: sourceReason.trim() };
    return { kind: "source", send: () => aiContentClient.approveSource(materialId, payload) };
  });
  const activity = (source: ContentSource) => void send(() => {
    const payload = { idempotency_key: crypto.randomUUID(), expected_version: source.version, is_active: !source.active, reason: sourceReason.trim() };
    return { kind: "source", send: () => aiContentClient.sourceActivity(materialId, source.id, payload) };
  });
  const submit = () => void send(() => {
    const values = tagsFromLines(tags);
    if (!description.trim() && !values.length) throw new Error("Enter a proposed description or at least one tag.");
    const payload = { idempotency_key: crypto.randomUUID(), expected_context_hash: snapshot.context.contextHash, provider: provider.trim(), model: model.trim(),
      prompt_version: prompt.trim(), description: description.trim() || null, tags: values, source_link_ids: [...sourceIds], reason: proposalReason.trim() };
    return { kind: "proposal", send: () => aiContentClient.submit(materialId, payload) };
  });
  const adopt = () => void send(() => {
    if (!selected || !confirmed) throw new Error("Review and confirm the proposed content first.");
    const draftId = selected.id;
    const payload = { idempotency_key: crypto.randomUUID(), expected_revision: snapshot.content.revision, expected_context_hash: selected.contextHash,
      description: reviewedDescription.trim() || null, tags: tagsFromLines(reviewedTags), reason: adoptionReason.trim() };
    return { kind: "adoption", send: () => aiContentClient.adopt(materialId, draftId, payload) };
  });
  const next = async () => {
    if (pageSending.current || sending.current || uncertain || !history.nextCursor) return;
    pageSending.current = true; setPaging(true); setError("");
    try { setHistory(await aiContentClient.drafts(materialId, history.nextCursor)); setSelected(null); }
    catch { setError("The next page could not be loaded. Retry or reload the workspace."); }
    finally { pageSending.current = false; setPaging(false); }
  };
  return <>
    {error && <p role="alert" className="field-error">{error}</p>}
    <fieldset disabled={frozen} className="ai-workspace"><legend>AI draft workspace</legend>
      <details><summary>Context available for this material</summary>
        <p>{snapshot.context.context.name} · {snapshot.context.context.brand.name}</p>
        <p>Categories: {snapshot.context.context.categories.map((item) => item.value).join(", ") || "None"}</p>
        <p>Collections: {snapshot.context.context.collections.map((item) => item.value).join(", ") || "None"}</p>
        <p>Approved source URLs:</p><ul>{snapshot.context.context.sourceUrls.map((source) => <li key={source.id}>{source.url}</li>)}</ul>
      </details>
      <details><summary>Approved source URLs</summary>
        <p>Only active, explicitly approved URLs belong to the AI context. Changing their availability invalidates existing checks and approvals.</p>
        {role === "ADMIN" && <label>Reason for source approval or availability change<textarea required maxLength={2000} value={sourceReason} onChange={(event) => setSourceReason(event.target.value)} /></label>}
        {snapshot.sources.length === 0 && <p>No source URLs approved.</p>}
        <ul>{snapshot.sources.map((source) => <li key={source.id}><span>{source.url} · {source.active ? "Active" : "Inactive"}</span>
          {role === "ADMIN" && <button type="button" className="button" disabled={!sourceReason.trim()}
            onClick={() => activity(source)}>{source.active ? "Deactivate" : "Reactivate"} {source.url}</button>}</li>)}</ul>
        {role === "ADMIN" && <form onSubmit={(event) => { event.preventDefault(); approve(); }}>
          <label>Source URL<input type="url" required maxLength={2048} value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://catalog.example/material" /></label>
          <p>Use a public HTTPS product page without credentials, query parameters or fragments. This app does not fetch the page.</p>
          <button className="button" disabled={!url.trim() || !sourceReason.trim()}>Approve source URL</button>
        </form>}
      </details>
      {editor && <details><summary>Record an AI proposal</summary>
        <p>Enter an externally prepared proposal and its declared origin. Saving it adds proposal history; your publication draft stays as saved.</p>
        <form onSubmit={(event) => { event.preventDefault(); submit(); }}>
          <label>AI provider<input required maxLength={100} value={provider} onChange={(event) => setProvider(event.target.value)} /></label>
          <label>AI model<input required maxLength={100} value={model} onChange={(event) => setModel(event.target.value)} /></label>
          <label>Prompt version<input required maxLength={100} value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
          <label>Proposed description<textarea maxLength={10000} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
          <label>Proposed tags, one per line<textarea value={tags} onChange={(event) => setTags(event.target.value)} /></label>
          <fieldset><legend>Approved URLs used for this proposal</legend>
            {snapshot.context.context.sourceUrls.length === 0 && <p>No approved source URLs available.</p>}
            {snapshot.context.context.sourceUrls.map((source) => <label key={source.id}><input type="checkbox" checked={sourceIds.includes(source.id)}
              onChange={(event) => setSourceIds(event.target.checked ? [...sourceIds, source.id] : sourceIds.filter((id) => id !== source.id))} />{source.url}</label>)}
          </fieldset>
          <label>Reason for recording proposal<textarea required maxLength={2000} value={proposalReason} onChange={(event) => setProposalReason(event.target.value)} /></label>
          <button className="button" disabled={!provider.trim() || !model.trim() || !prompt.trim() || !proposalReason.trim()}>Save AI proposal</button>
        </form>
      </details>}
      <section aria-label="AI proposal history"><h3>AI proposal history</h3>
        {history.items.length === 0 && <p>No AI proposals recorded.</p>}
        <ul>{history.items.map((draft) => {
          const current = draft.contextIsCurrent && draft.contextHash === snapshot.context.contextHash;
          return <li key={draft.id}><strong>{draft.provider} · {draft.model} · {draft.promptVersion}</strong> · <time dateTime={draft.createdAt}>{draft.createdAt}</time>
            <p>{current ? "Matches the loaded context." : "Context has changed; prepare a new proposal before adoption."}</p>
            <details><summary>Original proposal</summary><p className="ai-text">{draft.description || "No description"}</p><p>Tags: {draft.tags.join(", ") || "None"}</p>
              <p>{draft.reason}</p><ul>{draft.context.sourceUrls.filter((source) => draft.sourceIds.includes(source.id)).map((source) => <li key={source.id}>{source.url}</li>)}</ul></details>
            {editor && <button type="button" className="button" disabled={!current} onClick={() => {
              setSelected(draft); setReviewedDescription(draft.description ?? ""); setReviewedTags(draft.tags.join("\n")); setAdoptionReason(""); setConfirmed(false);
            }}>Compare and review proposal</button>}
          </li>;
        })}</ul>
        {history.nextCursor && <button type="button" className="button" onClick={() => void next()}>Older proposals</button>}
      </section>
      {selected && <section aria-label="Review AI proposal"><h3>Review AI proposal</h3>
        <div className="ai-comparison"><section aria-label="Currently saved content"><h4>Currently saved · revision {snapshot.content.revision}</h4>
          <p className="ai-text">{snapshot.content.description || "No description"}</p><p>Tags: {snapshot.content.tags.join(", ") || "None"}</p>
          <p>Credits: {snapshot.content.credits ?? "Not entered"}</p><p>Categories: {snapshot.content.categories.map((item) => item.value).join(", ") || "None"}</p>
          <p>Collections: {snapshot.content.collections.map((item) => item.value).join(", ") || "None"}</p></section>
          <form onSubmit={(event) => { event.preventDefault(); adopt(); }}>
            <h4>Content to adopt</h4>
            <label>Reviewed description<textarea maxLength={10000} value={reviewedDescription} onChange={(event) => { setReviewedDescription(event.target.value); setConfirmed(false); }} /></label>
            <label>Reviewed tags, one per line<textarea value={reviewedTags} onChange={(event) => { setReviewedTags(event.target.value); setConfirmed(false); }} /></label>
            <label>Reason for adopting proposal<textarea required maxLength={2000} value={adoptionReason} onChange={(event) => setAdoptionReason(event.target.value)} /></label>
            <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />I reviewed the text and tags. Adopt them and discard unsaved edits elsewhere on this page.</label>
            <p>Credits, categories and collections stay as saved. Adoption creates a new content revision and requires new checks and human approval.</p>
            <button className="button button--primary" disabled={!confirmed || !adoptionReason.trim()}>Adopt reviewed content</button>
          </form></div>
      </section>}
    </fieldset>
    {uncertain && <button className="button button--primary" disabled={pending} onClick={() => void send()}>Retry same AI request</button>}
    <button className="button" disabled={frozen} onClick={reload}>Reload AI workspace and discard its unsaved edits</button>
  </>;
}

export function MaterialAiPanel({ materialId, onAdopted, onSourcesChanged }: { materialId: string; onAdopted: () => void; onSourcesChanged: () => void }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null); const [pending, setPending] = useState(false); const [error, setError] = useState(false);
  const loading = useRef(false); const [version, setVersion] = useState(0);
  const load = async () => {
    if (loading.current) return;
    loading.current = true; setPending(true); setSnapshot(null); setError(false);
    try {
      const [context, content, sources, history] = await Promise.all([aiContentClient.context(materialId), catalogClient.content(materialId), aiContentClient.sources(materialId), aiContentClient.drafts(materialId)]);
      if (context.contentRevision !== content.revision) throw new Error("Content changed during loading");
      setSnapshot({ context, content, sources, history }); setVersion((value) => value + 1);
    } catch { setError(true); } finally { loading.current = false; setPending(false); }
  };
  return <article className="panel catalog-content ai-content" aria-label="AI proposals"><h2>AI proposals</h2>
    <p>Review source URLs and AI suggestions before using them in publication content. Live AI generation is not connected.</p>
    {error && <p role="alert">AI context and proposals could not be loaded. Check access or retry.</p>}
    {pending ? <p role="status">Loading AI workspace…</p> : snapshot
      ? <Workspace key={version} snapshot={snapshot} materialId={materialId} reload={() => void load()} onAdopted={onAdopted} onSourcesChanged={onSourcesChanged} />
      : <button className="button" onClick={() => void load()}>Load AI workspace</button>}
  </article>;
}
