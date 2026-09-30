import { useEffect, useRef, useState } from "react";
import type { Material } from "../api/materialDto";
import { materialLocalClient } from "../api/materialLocalClient";
import { sessionGeneration } from "../auth/sessionTransport";
import { useNavigationGuard } from "../navigationGuard";
import { FileCheckProgress } from "./FileCheckProgress";
import { useFileCheckProgress } from "./useFileCheckProgress";
import { FileCheckJobUnavailableError } from "../api/materialCheckJobs";

export type CheckReport = { report: string; reportPath: string | null; reportOpened: boolean };

export function CheckReportView({ result }: { result: CheckReport }) {
  const download = () => {
    const url = URL.createObjectURL(new Blob(["\uFEFF", result.report], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url; link.download = "reawote-material-check.txt";
    document.body.append(link); link.click(); link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <details className="material-check-summary" open><summary>Automatic file check report</summary>
    <textarea aria-label="Automatic file check report" className="material-check-report-text" readOnly rows={12} value={result.report} />
    <button type="button" className="button" onClick={download}>Download TXT report</button>
  </details>;
}

export function MaterialBulkCheck({ materials, disabled = false, onBusyChange, onChecked }: {
  materials: Material[]; disabled?: boolean; onBusyChange?: (busy: boolean) => void; onChecked?: () => void;
}) {
  const [pending, setPending] = useState(false), [error, setError] = useState("");
  const [report, setReport] = useState<CheckReport | null>(null);
  const sending = useRef(false), mounted = useRef(true);
  const checkProgress = useFileCheckProgress();
  useNavigationGuard(() => sending.current);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { onBusyChange?.(pending); return () => onBusyChange?.(false); }, [pending, onBusyChange]);
  const check = async () => {
    if (disabled || sending.current || !materials.length || materials.length > 100) return;
    const generation = sessionGeneration(), selection = materials.map(material => ({ id: material.id, updatedAt: material.updatedAt }));
    sending.current = true; setPending(true); setError(""); setReport(null);
    try {
      const result = await materialLocalClient.checkMany(selection, true, checkProgress.begin());
      if (mounted.current && generation === sessionGeneration()) { setReport(result); onChecked?.(); }
    } catch (cause) {
      if (mounted.current && generation === sessionGeneration()) setError(cause instanceof FileCheckJobUnavailableError ? cause.message : "The selected check could not finish. Refresh materials to see any saved result, check the source connection and try again.");
    } finally { sending.current = false; checkProgress.finish(); if (mounted.current) setPending(false); }
  };
  return <div className="material-bulk-check">
    <button type="button" className="button" disabled={disabled || pending || !materials.length || materials.length > 100} onClick={() => void check()}>
      {pending ? "Checking selected materials…" : `Check selected materials (${materials.length})`}
    </button>
    {pending && <FileCheckProgress progress={checkProgress.progress} resume={checkProgress.resume} total={materials.length} />}
    {error && <p role="alert" className="field-error">{error}</p>}
    {report && <CheckReportView result={report} />}
  </div>;
}
