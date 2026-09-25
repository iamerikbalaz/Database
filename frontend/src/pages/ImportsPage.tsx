import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import { importClient, importFailureMessage, importFindingMessage, readImportFile, type ImportColumns, type ImportConfirmation,
  type ImportField, type ImportGroup, type ImportInspection, type ImportLinks, type ImportPlanRequest,
  type ImportPreview, type ImportResult, type ImportSource } from "../api/importClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ImportHistory } from "../components/ImportHistory";
import { ImportRows } from "../components/ImportRows";
import { ErrorState, LoadingState } from "../components/PageState";

const fields: { key: ImportField; label: string }[] = [
  { key: "identity", label: "Technical identity" }, { key: "name", label: "Material name" },
  { key: "project", label: "Project label" }, { key: "brand", label: "Brand label" }, { key: "processor", label: "Processor label" },
  { key: "folder", label: "Existing folder path" },
];
const groups: ImportGroup[] = ["project", "brand", "processor"];
const groupKeys = { project: "projects", brand: "brands", processor: "processors" } as const;
const blankColumns = (): ImportColumns => ({ identity: "", name: "", project: "", brand: "", processor: "", folder: null });
const blankLinks = (): ImportLinks => ({ projects: {}, brands: {}, processors: {} });

export function ImportsPage(props: { client: ApiClient; navigate: (path: string) => void }) {
  if (useSession()?.session.user.role !== "ADMIN") return <section><h1>Access restricted</h1><p>Historical imports require an administrator.</p></section>;
  return <ImportWorkspace {...props} />;
}
function ImportWorkspace({ client, navigate }: { client: ApiClient; navigate: (path: string) => void }) {
  const [file, setFile] = useState<File>();
  const [format, setFormat] = useState<"CSV" | "XLSX">("CSV"); const [delimiter, setDelimiter] = useState<"" | "," | ";">("");
  const [sheet, setSheet] = useState(""); const [source, setSource] = useState<ImportSource>();
  const [inspection, setInspection] = useState<ImportInspection>(); const [columns, setColumns] = useState(blankColumns);
  const [links, setLinks] = useState(blankLinks); const [preview, setPreview] = useState<ImportPreview>();
  const [result, setResult] = useState<ImportResult>(); const [historyVersion, setHistoryVersion] = useState(0);
  const [reason, setReason] = useState(""); const [acknowledged, setAcknowledged] = useState(false);
  const [pending, setPending] = useState(false); const [uncertain, setUncertain] = useState(false); const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const sending = useRef(false); const mounted = useRef(true);
  const planned = useRef<ImportPlanRequest | undefined>(undefined); const confirmation = useRef<ImportConfirmation | undefined>(undefined);
  const fileInput = useRef<HTMLInputElement>(null);
  const load = useCallback(async () => {
    const [projects, brands, companies, processors] = await Promise.all([client.getProjects(), client.getBrands(), client.getCompanies(), client.getInternalUsers()]);
    return { projects, brands, companies, processors };
  }, [client]);
  const references = useResource(load);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!uncertain && !confirming) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [uncertain, confirming]);
  const invalidatePreview = () => { planned.current = undefined; setPreview(undefined); setReason(""); setAcknowledged(false); setError(""); };
  const clearSource = () => {
    setSource(undefined); setInspection(undefined); setColumns(blankColumns()); setLinks(blankLinks()); invalidatePreview();
  };
  const run = async (operation: () => Promise<void>, confirming = false) => {
    if (sending.current) return;
    sending.current = true; setPending(true); setConfirming(confirming); setError("");
    try { await operation(); }
    catch (cause) {
      if (!mounted.current) return;
      if (confirming && (!(cause instanceof ApiError) || cause.status >= 500)) {
        setUncertain(true); setError("The import outcome is unknown. Retry the same confirmation before starting another import. Completed batches can also be checked in import history.");
      } else {
        if (confirming) { confirmation.current = undefined; setUncertain(false); invalidatePreview(); }
        setError(importFailureMessage(cause, confirming));
      }
    } finally { sending.current = false; if (mounted.current) { setPending(false); setConfirming(false); } }
  };
  const inspect = () => void run(async () => {
    if (!file || (format === "CSV" && !delimiter)) return;
    const body: ImportSource = { format, data: source?.data ?? await readImportFile(file),
      ...(format === "CSV" ? { delimiter: delimiter as "," | ";" } : sheet ? { sheet } : {}) };
    const value = await importClient.inspect(body);
    if (!mounted.current) return;
    setSource(body); setInspection(value); setColumns(blankColumns()); setLinks(blankLinks()); invalidatePreview();
  });
  const mapColumns = () => void run(async () => {
    const selected = Object.values(columns).filter((value) => value !== null);
    if (!source || selected.some((value) => !value) || new Set(selected).size !== selected.length) return;
    const value = await importClient.inspect(source, columns);
    if (!mounted.current) return;
    if (value.requiresSheet || !value.mappingValues) throw new Error("Missing source mappings");
    setInspection(value);
    setLinks({ projects: Object.fromEntries(value.mappingValues.project.map((key) => [key, ""])),
      brands: Object.fromEntries(value.mappingValues.brand.map((key) => [key, ""])), processors: Object.fromEntries(value.mappingValues.processor.map((key) => [key, ""])) });
    invalidatePreview();
  });
  const prepare = () => void run(async () => {
    if (!source) return;
    const body = structuredClone({ source, columns, links });
    const value = await importClient.preview(body);
    if (!mounted.current) return;
    if (inspection?.requiresSheet !== false || value.sourceHash !== inspection.hash) throw new Error("Source changed");
    planned.current = body; setPreview(value); setReason(""); setAcknowledged(false);
  });
  const confirm = () => void run(async () => {
    if (!confirmation.current) {
      if (!planned.current || !preview?.ready || !preview.hash || !reason.trim() || !acknowledged) return;
      confirmation.current = structuredClone({ ...planned.current, idempotency_key: crypto.randomUUID(), expected_preview_hash: preview.hash,
        acknowledge_unverified: true, reason: reason.trim() });
    }
    const value = await importClient.confirm(confirmation.current);
    if (!mounted.current) return;
    if (!preview || value.sourceHash !== preview.sourceHash || value.rowCount !== preview.rowCount || value.rows.some((row, index) => {
      const expected = preview.rows[index];
      return !expected || Object.entries(expected).some(([key, value]) => row[key as keyof typeof expected] !== value);
    })) throw new Error("The confirmed rows do not match the reviewed batch");
    confirmation.current = undefined; setUncertain(false); setResult(value); setHistoryVersion((value) => value + 1);
  }, true);
  const locked = pending || uncertain || Boolean(result);
  const mappedValues = inspection?.requiresSheet === false ? inspection.mappingValues : undefined;
  const mappingsComplete = mappedValues && groups.every((group) => mappedValues[group].every((label) =>
    Object.hasOwn(links[groupKeys[group]], label) && Boolean(links[groupKeys[group]][label])));
  const company = (id: string) => references.data?.companies.find((item) => item.id === id)?.name ?? "Unavailable company";
  const activeCompany = (id: string) => references.data?.companies.some((item) => item.id === id && item.status === "active");
  return <section className="imports-page"><div className="page-heading"><div><p className="eyebrow">Historical PBR records</p><h1>Import materials</h1></div></div>
    <p>Keep historical identities and select the existing brand and processor for each source label. Historical materials can have no project; a project can be assigned later. Imported materials start in progress and require normal source checks and approval.</p>
    {error && <p className="field-error" role="alert">{error}</p>}
    {pending && <p role="status">{confirming ? "Confirming import…" : "Checking import…"}</p>}
    {uncertain && <button className="button button--primary" disabled={pending} onClick={confirm}>Retry same import confirmation</button>}
    <article className="panel"><h2>1. Inspect source</h2>
      <p>UTF-8 CSV or XLSX · up to 4 MiB, 2,000 records, 32 columns. Use literal values; formulas, macros and external links are rejected. XLSX formatting is not interpreted.</p>
      <form onSubmit={(event) => { event.preventDefault(); inspect(); }}><fieldset disabled={locked}><legend>Source file and reading options</legend>
        <label>Historical source file<input ref={fileInput} type="file" accept=".csv,.xlsx" required onChange={(event) => {
          setFile(event.target.files?.[0]); setSheet(""); clearSource();
        }} /></label>
        {file && (file.size < 1 || file.size > 4 * 1024 ** 2) && <p className="field-error">Choose a nonempty file up to 4 MiB.</p>}
        <label>Source format<select value={format} onChange={(event) => { setFormat(event.target.value as "CSV" | "XLSX"); setSheet(""); clearSource(); }}>
          <option value="CSV">CSV</option><option value="XLSX">XLSX</option></select></label>
        {format === "CSV" && <label>CSV delimiter<select required value={delimiter} onChange={(event) => { setDelimiter(event.target.value as typeof delimiter); clearSource(); }}>
          <option value="">Choose delimiter</option><option value=";">Semicolon (;)</option><option value=",">Comma (,)</option></select></label>}
        {format === "XLSX" && inspection && <label>Worksheet<select required value={sheet} onChange={(event) => {
          setSheet(event.target.value); setColumns(blankColumns()); setLinks(blankLinks()); invalidatePreview();
          setInspection({ format: "XLSX", requiresSheet: true, sheets: inspection.sheets });
        }}><option value="">Choose worksheet</option>{inspection.sheets.map((name) => <option key={name}>{name}</option>)}</select></label>}
        <button className="button" disabled={!file || !file.size || file.size > 4 * 1024 ** 2 || (format === "CSV" && !delimiter) || (format === "XLSX" && Boolean(inspection) && !sheet)}>Inspect source</button>
      </fieldset></form>
      {inspection?.requiresSheet === false && <><p>{inspection.rowCount} source records. Sample of the first {inspection.sample.length} records:</p>
        <div className="table-scroll" role="region" aria-label="Source sample" tabIndex={0}><table><thead><tr><th>Source row</th>{inspection.headers.map((header) => <th key={header}>{header}</th>)}</tr></thead>
          <tbody>{inspection.sample.map((row) => <tr key={row.row}><td>{row.row}</td>{row.values.map((value, index) => <td key={index}>{value}</td>)}</tr>)}</tbody></table></div></>}
    </article>
    {inspection?.requiresSheet === false && <article className="panel"><h2>2. Map source columns</h2>
      <form onSubmit={(event) => { event.preventDefault(); mapColumns(); }}><fieldset disabled={locked}><legend>Choose distinct source columns</legend>
        <label><input type="checkbox" checked={columns.project === null} onChange={(event) => {
          setColumns({ ...columns, project: event.target.checked ? null : "" }); setInspection({ ...inspection, mappingValues: undefined }); setLinks(blankLinks()); invalidatePreview();
        }} />Import historical materials without a project</label>
        <label><input type="checkbox" checked={columns.folder !== null} onChange={(event) => {
          setColumns({ ...columns, folder: event.target.checked ? "" : null }); setInspection({ ...inspection, mappingValues: undefined }); setLinks(blankLinks()); invalidatePreview();
        }} />Record existing folder references without creating or changing folders</label>
        {columns.folder !== null && <p>Use paths relative to the configured material library. References remain unverified until a source check succeeds.</p>}
        {fields.filter(({ key }) => (key !== "project" || columns.project !== null) && (key !== "folder" || columns.folder !== null)).map(({ key, label }) => <label key={key}>{label} column<select required value={columns[key] ?? ""} onChange={(event) => {
          setColumns({ ...columns, [key]: event.target.value }); setInspection({ ...inspection, mappingValues: undefined }); setLinks(blankLinks()); invalidatePreview();
        }}><option value="">Choose column</option>{inspection.headers.map((header) => <option key={header} disabled={Object.entries(columns).some(([field, value]) => field !== key && value === header)}>{header}</option>)}</select></label>)}
        <button className="button" disabled={Object.values(columns).some((value) => value !== null && !value) || new Set(Object.values(columns).filter((value) => value !== null)).size !== Object.values(columns).filter((value) => value !== null).length}>Load source labels</button>
      </fieldset></form>
    </article>}
    {mappedValues && <article className="panel"><h2>3. Select existing records</h2>
      <p>Project company and brand company can differ. Compare both companies before confirming.</p>
      {references.error ? <ErrorState message="Existing records could not be loaded." retry={references.retry} /> : !references.data ? <LoadingState label="Loading existing records…" /> :
        <form onSubmit={(event) => { event.preventDefault(); prepare(); }}><fieldset disabled={locked}><legend>Explicit source label mappings</legend>
          {groups.map((group) => <section key={group} aria-label={`${group} mappings`}><h3>{group === "project" ? "Projects" : group === "brand" ? "Brands" : "Processors"}</h3>
            {mappedValues[group].map((label) => <label key={label}>{group[0].toUpperCase() + group.slice(1)}: {label}
              <select required value={links[groupKeys[group]][label] ?? ""} onChange={(event) => {
                setLinks({ ...links, [groupKeys[group]]: { ...links[groupKeys[group]], [label]: event.target.value } }); invalidatePreview();
              }}><option value="">Choose existing {group}</option>
                {group === "project" ? references.data!.projects.filter((item) => activeCompany(item.companyId)).map((item) => <option value={item.id} key={item.id}>{item.name} · {item.number} · {company(item.companyId)}</option>) :
                  group === "brand" ? references.data!.brands.filter((item) => item.isActive && activeCompany(item.companyId)).map((item) => <option value={item.id} key={item.id}>{item.name} · {item.folderPrefix} · {company(item.companyId)}</option>) :
                    references.data!.processors.filter((item) => item.isActive && item.role === "PROCESSOR").map((item) => <option value={item.id} key={item.id}>{item.displayName} · {item.email}</option>)}
              </select></label>)}
          </section>)}
          <button className="button button--primary" disabled={!mappingsComplete}>Prepare import preview</button>
          <button className="button" type="button" onClick={() => { invalidatePreview(); references.retry(); }}>Reload existing records</button>
        </fieldset></form>}
    </article>}
    {preview && !result && <article className="panel" aria-label="Import preview"><h2>4. Review and confirm</h2>
      <p>{preview.rowCount} source records · {preview.ready ? "Ready for your confirmation" : "Import blocked; correct every finding first"}</p>
      {preview.ignoredColumns.length > 0 && <p>Unused columns will not be imported: {preview.ignoredColumns.join(", ")}.</p>}
      {preview.findings.length > 0 && <details open><summary>{preview.findings.length} blocking findings</summary><ul>{preview.findings.slice(0, 100).map((finding, index) =>
        <li key={index}>Row {finding.row}, {finding.field}: {importFindingMessage(finding.code)}</li>)}</ul>
        {preview.findings.length > 100 && <p>Showing the first 100 findings. Correct these and prepare another preview.</p>}</details>}
      <ImportRows key={preview.hash ?? preview.sourceHash} data={preview} navigate={navigate} />
      {preview.ready && <form onSubmit={(event) => { event.preventDefault(); confirm(); }}><fieldset disabled={locked}><legend>Confirm this exact batch</legend>
        <label>Reason for historical import<textarea required maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <label className="import-acknowledgement"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
          I checked the identities and mappings. These records remain unverified and need normal source checks and approval.</label>
        <button className="button button--primary" disabled={!acknowledged || !reason.trim()}>Confirm import of {preview.rowCount} materials</button>
      </fieldset></form>}
    </article>}
    {result && <article className="panel" aria-label="Completed import"><h2>Import completed</h2><p role="status">{result.rowCount} materials imported. Their original identities and mappings are recorded in import history.</p>
      <p>Next, open each material to link its source folder and complete normal checks.</p><ImportRows data={result} navigate={navigate} />
      <button className="button" onClick={() => { setResult(undefined); setFile(undefined); if (fileInput.current) fileInput.current.value = ""; setSheet(""); clearSource(); references.retry(); }}>Start another import</button>
    </article>}
    <ImportHistory key={historyVersion} navigate={navigate} />
  </section>;
}
