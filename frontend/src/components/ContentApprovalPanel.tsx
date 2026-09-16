import { useCallback, useRef, useState } from "react";
import { contentReviewClient, type ContentApprovalPayload, type ContentReview } from "../api/contentReviewClient";
import { ApiError } from "../api/errors";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ErrorState, LoadingState } from "./PageState";

const findings: Record<string, string> = {
  CONTENT_DRAFT_REQUIRED: "Save a publication draft first.", CONTENT_CREDITS_REQUIRED: "Enter credits.",
  CONTENT_CATEGORIES_REQUIRED: "Select at least one online category.", CONTENT_BRAND_INACTIVE: "The published brand is inactive.",
  CONTENT_CATALOG_VALUE_INACTIVE: "Remove or replace inactive categories and collections.",
  CONTENT_COLLECTION_BRAND_MISMATCH: "A collection belongs to a different brand.",
  CONTENT_DESCRIPTION_EMPTY: "The description is empty.", CONTENT_TAGS_EMPTY: "The tag list is empty.",
};

function SavedContent({ snapshot, revision }: { snapshot: ContentReview["snapshot"]; revision: number }) {
  const { content, brandName, brandIdentifier, identity, materialName } = snapshot;
  return <dl><dt>Saved revision</dt><dd>{revision}</dd><dt>Material</dt><dd>{materialName} · {identity}</dd>
    <dt>Brand</dt><dd>{brandName} · {brandIdentifier}</dd><dt>Description</dt><dd>{content.description || "Empty"}</dd>
    <dt>Credits</dt><dd>{content.credits ?? "Missing"}</dd><dt>Tags</dt><dd>{content.tags.join(", ") || "Empty"}</dd>
    <dt>Online categories</dt><dd>{content.categories.map((item) => item.value).join(", ") || "None"}</dd>
    <dt>Brand collections</dt><dd>{content.collections.map((item) => item.value).join(", ") || "None"}</dd></dl>;
}

export function ContentApprovalPanel({ materialId, refreshVersion = 0, onChanged }: { materialId: string; refreshVersion?: number; onChanged: () => void }) {
  const load = useCallback(() => {
    // A new generation of detail reads also refreshes this independent report.
    void refreshVersion;
    return contentReviewClient.review(materialId);
  }, [materialId, refreshVersion]);
  const resource = useResource(load), role = useSession()?.session.user.role;
  const allowed = role === "ADMIN" || role === "LEADERSHIP";
  const [preview, setPreview] = useState<ContentReview | null>(null);
  const [note, setNote] = useState(""), [ack, setAck] = useState(false);
  const [error, setError] = useState(""), [pending, setPending] = useState(false), [uncertain, setUncertain] = useState(false);
  const sending = useRef(false), payload = useRef<ContentApprovalPayload | null>(null);
  const [history, setHistory] = useState<Awaited<ReturnType<typeof contentReviewClient.history>> | null>(null);
  const [historyError, setHistoryError] = useState(false);
  const approve = async () => {
    if (sending.current || !preview) return;
    sending.current = true; setPending(true); setError("");
    payload.current ??= { idempotency_key: crypto.randomUUID(), expected_revision: preview.revision,
      expected_context_hash: preview.contextHash, warnings_acknowledged: ack, note: note.trim() || null };
    try {
      await contentReviewClient.approve(materialId, payload.current);
      payload.current = null; setUncertain(false); setPreview(null); setHistory(null); resource.retry(); onChanged();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        payload.current = null; setUncertain(false); setPreview(null); resource.retry();
        setError("Approval was rejected. Review the current saved content, requirements and your role before trying again.");
      } else {
        setUncertain(true); setError("The approval outcome is unknown. Retry the same decision before making another one.");
      }
    } finally { sending.current = false; setPending(false); }
  };
  return <article className="panel catalog-content" aria-label="Content approval"><h2>Content approval</h2>
    <p>Review the saved content before approving it. This decision does not publish the material. Source or catalog changes can require a new decision.</p>
    {error && <p role="alert">{error}</p>}
    {resource.error ? <ErrorState message="Content review could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading content review…" /> : <>
      <p>{resource.data.approval ? "Current content is approved." : "Current content requires approval."}</p>
      {resource.data.approval && <p>Approved revision {resource.data.approval.revision} · <time dateTime={resource.data.approval.createdAt}>{resource.data.approval.createdAt}</time> · reviewer {resource.data.approval.actorId}</p>}
      {resource.data.errors.length > 0 && <ul aria-label="Content blockers">{resource.data.errors.map((code) => <li key={code}>{findings[code] ?? code}</li>)}</ul>}
      {resource.data.warnings.length > 0 && <ul aria-label="Content warnings">{resource.data.warnings.map((code) => <li key={code}>{findings[code] ?? code}</li>)}</ul>}
      {allowed && resource.data.canApprove && !preview && <button className="button" onClick={() => { setPreview(resource.data!); setAck(false); setNote(""); }}>Review saved content</button>}
      {!allowed && <p>Leadership or an administrator can approve publication content.</p>}
      {preview && <section aria-label="Saved content to approve"><h3>Confirm this saved revision</h3>
        <p>Unsaved changes in the draft editor are excluded from this decision.</p><SavedContent snapshot={preview.snapshot} revision={preview.revision} />
        {preview.snapshot.sourceHash === null && <p>Source files have not been verified for this draft. A later source check will require content review again.</p>}
        <form onSubmit={(event) => { event.preventDefault(); void approve(); }}>
          <fieldset disabled={pending || uncertain}><legend>Content decision</legend>
            <label>Content approval note<textarea value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} /></label>
            {preview.warnings.length > 0 && <label><input type="checkbox" checked={ack} onChange={(event) => setAck(event.target.checked)} />I reviewed the empty description or tags and accept these warnings</label>}
          </fieldset>
          <button className="button button--primary" disabled={pending || (preview.warnings.length > 0 && (!ack || !note.trim()))}>{uncertain ? "Retry same content approval" : "Confirm content approval"}</button>
          <button type="button" className="button" disabled={pending || uncertain} onClick={() => setPreview(null)}>Cancel content approval</button>
        </form>
      </section>}
      <button className="button" disabled={pending || uncertain} onClick={() => { setPreview(null); resource.retry(); }}>Reload content approval</button>
      <details><summary>Content approval history</summary>
        <button className="button" onClick={() => { setHistoryError(false); void contentReviewClient.history(materialId).then(setHistory, () => setHistoryError(true)); }}>Load content approval history</button>
        {historyError && <p role="alert">Content approval history could not be loaded.</p>}
        {history && <ul>{history.map((item) => <li key={item.id}>Approved revision {item.revision} · {item.createdAt} · reviewer {item.actorId} · {item.note || "No note"}
          <details><summary>Reviewed content</summary><SavedContent snapshot={item.snapshot} revision={item.revision} /></details></li>)}</ul>}
      </details>
    </>}
  </article>;
}
