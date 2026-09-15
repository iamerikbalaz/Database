import { useCallback, useRef, useState } from "react";
import type { Material } from "../api/materialDto";
import { ApiError } from "../api/errors";
import { technicalClient, type ApprovalKind } from "../api/technicalClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ErrorState, LoadingState } from "./PageState";

const labels: Record<string, string> = {
  SOURCE_METADATA_MISSING: "Root metadata.txt is missing", PREVIEW_MISSING: "No preview files are available",
  NORMAL_MAP_MISSING: "No normal map is available", SURFACE_RESPONSE_MAP_MISSING: "No roughness or gloss map is available",
  SOURCE_METADATA_INVALID_FORMAT: "Source metadata could not be parsed", MASTER_DIMENSIONS_DIFFER: "Actual dimensions differ from the master folder name",
  IMAGE_UNREADABLE: "Image cannot be decoded", COLOR_MAP_REQUIRED: "A readable color map is required",
  MAP_16BIT_REQUIRED: "This map requires 16-bit data", MAP_DIMENSIONS_MISMATCH: "Map dimensions do not match the color map",
  MAP_FILENAME_INVALID: "Map filename does not match the material identity", MAP_SHORTCUT_DUPLICATE: "Map shortcut is duplicated",
  MASTER_BELOW_1K: "The color map is smaller than 1K", HEX_COLOR_MISSING: "Source metadata does not provide a color",
};
function failure(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 409) return "The material or technical report changed, or an approval requirement is not met. Review the current report before trying again.";
    if (error.status === 422) return "The source could not be verified, or warnings need an acknowledgment and note. Review the current findings.";
    if (error.status === 403) return "Your role does not allow this operation.";
    if (error.status === 503) return "Technical validation is temporarily unavailable. Try again when the worker is available.";
  }
  return "The result could not be confirmed. Retry to check the same operation, or reload the review.";
}

export function MaterialTechnicalPanel({ material, onChanged }: { material: Material; onChanged: () => Promise<boolean> }) {
  const role = useSession()?.session.user.role;
  const load = useCallback(() => technicalClient.current(material.id), [material.id]);
  const resource = useResource(load);
  const [pending, setPending] = useState(false); const sending = useRef(false);
  const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const [note, setNote] = useState(""); const [acknowledged, setAcknowledged] = useState(false);
  const request = useRef<{ fingerprint: string; key: string } | null>(null);
  const run = async (kind: ApprovalKind | "CHECK") => {
    const current = resource.data;
    if (!current || sending.current) return;
    sending.current = true; setPending(true); setError(""); setNotice("");
    const fingerprint = JSON.stringify({ kind, generation: current.review.generation,
      report: kind === "CHECK" ? null : current.validation?.id, note: kind === "CHECK" ? "" : note.trim(), acknowledged: kind !== "CHECK" && acknowledged });
    try {
      if (request.current?.fingerprint !== fingerprint) request.current = { fingerprint, key: crypto.randomUUID() };
      if (kind === "CHECK") await technicalClient.run(material.id, current.review.generation, request.current.key);
      else await technicalClient.approve(material.id, current, kind, request.current.key, note, acknowledged);
      request.current = null; setNote(""); setAcknowledged(false);
      resource.retry(); const refreshed = await onChanged();
      setNotice((kind === "CHECK" ? "Technical report saved." : kind === "TECHNICAL" ? "Technical approval saved for this revision." : "Publication approval saved for this revision.") + (refreshed ? "" : " Reload the material to refresh its status."));
    } catch (cause) {
      setError(failure(cause));
      if (cause instanceof ApiError) { request.current = null; setAcknowledged(false); resource.retry(); await onChanged(); }
    } finally { sending.current = false; setPending(false); }
  };
  const data = resource.data; const check = data?.validation;
  const technical = data?.approvals.find((item) => item.kind === "TECHNICAL");
  const publication = data?.approvals.find((item) => item.kind === "PUBLICATION");
  const mayTechnical = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const mayPublication = role === "ADMIN" || role === "LEADERSHIP";
  const ready = material.workflowStatus === "DONE" && check?.canApprove;
  const warningsAccepted = !check?.warnings.length || (acknowledged && Boolean(note.trim()));
  return <article className="panel material-review" aria-label="Technical review">
    <h2>Technical checks and approvals</h2>
    <p>Production Done, technical approval and publication approval are separate steps. Each approval checks the source again and applies to this revision only.</p>
    {error && <p role="alert" className="field-error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {resource.error ? <ErrorState message="Technical review could not be loaded." retry={resource.retry} /> : !data ? <LoadingState label="Loading technical review…" /> : <>
      <dl className="info-list"><div><dt>Technical check</dt><dd>{check ? check.canApprove ? check.warnings.length ? "Passed with warnings" : "Passed" : "Corrections required" : "Not checked for the current revision"}</dd></div>
        <div><dt>Technical approval</dt><dd>{technical ? "Approved for this revision" : "Not approved"}</dd></div>
        <div><dt>Publication approval</dt><dd>{publication ? "Approved for this revision" : "Not approved"}</dd></div></dl>
      {role !== "LEADERSHIP" && <button className="button" disabled={pending || !material.folderPath} onClick={() => void run("CHECK")}>{pending ? "Working…" : "Run technical checks"}</button>}
      {!material.folderPath && <p>Link the material folder before checking its maps.</p>}
      {check && <>
        <p>Checked <time dateTime={check.createdAt}>{check.createdAt}</time></p>
        {check.errors.length > 0 && <section aria-label="Technical errors"><h3>Errors to fix</h3><ul>{check.errors.map((item, index) => <li key={index}>{labels[item.code] ?? item.code.replaceAll("_", " ")}{item.path && <> · <code>{item.path}</code></>}</li>)}</ul></section>}
        {check.warnings.length > 0 && <section aria-label="Technical warnings"><h3>Warnings to review</h3><ul>{check.warnings.map((item, index) => <li key={index}>{labels[item.code] ?? item.code.replaceAll("_", " ")}{item.path && <> · <code>{item.path}</code></>}</li>)}</ul></section>}
        <details><summary>Verified maps ({check.images.length})</summary><div className="table-scroll"><table><thead><tr><th>Map</th><th>Dimensions</th><th>Depth</th><th>Format</th></tr></thead>
          <tbody>{check.images.map((image) => <tr key={image.path}><td>{image.map}</td><td>{image.width} × {image.height}</td><td>{image.bits} bit</td><td>{image.format}</td></tr>)}</tbody></table></div></details>
        {material.workflowStatus !== "DONE" && <p>Finish production before requesting approvals.</p>}
        {ready && ((mayTechnical && !technical) || (mayPublication && technical && !publication)) && <fieldset disabled={pending}><legend>Approve the current revision</legend>
          <label>Approval note<textarea maxLength={2000} value={note} onChange={(event) => setNote(event.target.value)} /></label>
          {check.warnings.length > 0 && <label><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />I reviewed the warnings and explained their acceptance in the note.</label>}
          {mayTechnical && !technical && <button className="button button--primary" disabled={!warningsAccepted} onClick={() => void run("TECHNICAL")}>Approve technically</button>}
          {mayPublication && technical && !publication && <button className="button button--primary" disabled={!warningsAccepted} onClick={() => void run("PUBLICATION")}>Approve for publication</button>}
        </fieldset>}
        {mayPublication && !technical && <p>Technical approval is required before publication approval.</p>}
      </>}
      {data.approvals.map((item) => <p key={item.id}>{item.kind === "TECHNICAL" ? "Technical" : "Publication"} approval · <time dateTime={item.createdAt}>{item.createdAt}</time> · reviewer {item.actorId}{item.note && <> · {item.note}</>}</p>)}
      {publication && <p>Publication approval does not upload or publish the material.</p>}
      <button className="button" disabled={pending} onClick={() => { setAcknowledged(false); resource.retry(); }}>Reload technical review</button>
    </>}
  </article>;
}
