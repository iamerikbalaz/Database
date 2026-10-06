import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { storedFileCheckClient } from "../api/storedFileCheckClient";
import type { Material } from "../api/materialDto";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { sessionGeneration } from "../auth/sessionTransport";
import { CheckReportView } from "./MaterialBulkCheck";
import "./AutomaticFileCheckStatus.css";

function StoredCheckDialog({ material, onClose }: { material: Pick<Material, "id" | "materialName">; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), returnFocus = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  const actor = useSession()?.session.user.id;
  const generation = sessionGeneration();
  const load = useCallback(async () => {
    void actor;
    const result = await storedFileCheckClient.get(material.id);
    if (generation !== sessionGeneration()) throw new Error("Session changed");
    return result;
  }, [material.id, actor, generation]);
  const resource = useResource(load), report = resource.data?.report;
  useEffect(() => { dialog.current?.showModal(); const previous = returnFocus.current; return () => previous?.focus(); }, []);
  const downloadReport = report ? [
    `Material: ${material.materialName}`,
    `Checked: ${report.checkedAt ?? "Unknown"}`,
    `Profile: ${report.profile ?? "Unknown"}`,
    report.isCurrent ? "Status at last recorded check: " + report.status : "Historical report — material changed after this check. Run a new check for the current state.",
    ...(report.issues.length ? ["Issues", ...report.issues.map(issue => `• ${issue}`)] : []),
    ...(report.warnings.length ? ["Warnings", ...report.warnings.map(warning => `• ${warning}`)] : []),
    "", report.text,
  ].join("\n") : "";
  return createPortal(<dialog ref={dialog} className="stored-file-check-dialog" aria-labelledby="stored-file-check-heading" onClick={event => event.stopPropagation()} onMouseDown={event => event.stopPropagation()} onCancel={event => { event.preventDefault(); onClose(); }}>
    <div className="stored-file-check-heading"><h2 id="stored-file-check-heading">Automatic file check</h2><button className="button" type="button" onClick={onClose}>Close report</button></div>
    <p className="stored-file-check-material">{material.materialName}</p>
    {resource.error ? <div role="alert"><p>The saved report could not be loaded. Check your access and try again.</p><button className="button" type="button" onClick={resource.retry}>Retry loading report</button></div> : !resource.data ? <p role="status">Loading saved report…</p> : !report ? <p role="status">No saved automatic file check report is available for this material.</p> : <>
      <dl className="stored-file-check-meta"><div><dt>Checked</dt><dd>{report.checkedAt ? new Date(report.checkedAt).toLocaleString() : "Unknown"}</dd></div>
        <div><dt>Profile</dt><dd>{report.profile ?? "Unknown"}{report.complete ? " · Full check" : " · Preliminary check"}</dd></div></dl>
      {!report.isCurrent && <p className="stored-file-check-stale" role="status">Historical report: the material changed after this check. The current automatic check status is {resource.data.currentStatus === "NOT_CHECKED" ? "not checked" : resource.data.currentStatus === "ISSUES" ? "issues" : "OK"}.</p>}
      {report.isCurrent && <p className="stored-file-check-caption">Last stored result. Opening this report does not scan the material again.</p>}
      <CheckReportView result={{ report: downloadReport, reportPath: null, reportOpened: false }} />
    </>}
  </dialog>, document.body);
}

export function AutomaticFileCheckStatus({ material, allowLastReport = false }: { material: Pick<Material, "id" | "materialName" | "automaticFileCheckStatus" | "automaticFileCheckedAt">; allowLastReport?: boolean }) {
  const [open, setOpen] = useState(false);
  const status = material.automaticFileCheckStatus ?? "NOT_CHECKED";
  const label = status === "NOT_CHECKED" ? "not checked" : status === "ISSUES" ? "issues" : "OK";
  const className = `automatic-file-check automatic-file-check--${status.toLowerCase()}`;
  return <>
    {status !== "NOT_CHECKED" ? <button type="button" className={`${className} automatic-file-check-link`} aria-label={`Open automatic file check report for ${material.materialName}`} title="Open the last saved automatic file check report" onClick={() => setOpen(true)}>{label}</button> : <span className={className}>{label}</span>}
    {allowLastReport && status === "NOT_CHECKED" && <button type="button" className="stored-file-check-last" onClick={() => setOpen(true)}>Last report</button>}
    {open && <StoredCheckDialog key={material.id} material={material} onClose={() => setOpen(false)} />}
  </>;
}
