import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import importTemplateUrl from "../assets/material-import-template.csv?url&no-inline";
import { ApiError } from "../api/errors";
import { directoryClient } from "../api/directoryClient";
import { importClient, importFailureMessage, importFindingMessage, readImportFile, type ImportColumns, type ImportConfirmation,
  type ImportField, type ImportGroup, type ImportInspection, type ImportLinks, type ImportPlanRequest,
  type ImportPreview, type ImportResult, type ImportSource } from "../api/importClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { ImportHistory } from "../components/ImportHistory";
import { ImportRows } from "../components/ImportRows";
import { MaterialAiBriefDialog } from "../components/MaterialAiBriefDialog";
import { ErrorState, LoadingState } from "../components/PageState";

const fields: { key: ImportField; label: string }[] = [
  { key: "identity", label: "Technical identity" }, { key: "name", label: "Material name" },
  { key: "project", label: "Order label" }, { key: "brand", label: "Customer label" }, { key: "processor", label: "Processor label" },
  { key: "folder", label: "Existing folder path" },
];
const groups: ImportGroup[] = ["project", "brand", "processor"];
const propertyFields = [
  { key: "color", label: "HEX color" }, { key: "sample_size", label: "Sample size (cm)" },
  { key: "done", label: "Done" }, { key: "checked", label: "Checked" }, { key: "note", label: "Note" },
  { key: "brand_identifier", label: "Brand identifier" },
] as const;
const groupKeys = { project: "projects", brand: "brands", processor: "processors" } as const;
const groupLabels = { project: "Order", brand: "Customer", processor: "Processor" } as const;
const blankColumns = (): ImportColumns => ({ identity: "", name: "", project: "", brand: "", processor: "", folder: null,
  color: null, sample_size: null, done: null, checked: null, note: null, brand_identifier: null });
const blankLinks = (): ImportLinks => ({ projects: {}, brands: {}, processors: {} });

export function ImportsPage(props: { client: ApiClient; navigate: (path: string) => void }) {
  const role = useSession()?.session.user.role;
  const [aiOpen, setAiOpen] = useState(false);
  const [aiChanged, setAiChanged] = useState(false);
  if (role !== "ADMIN" && role !== "PRODUCTION_LEAD") return <section><h1>Access restricted</h1><p>Imports require an administrator or production lead. Historical CSV/XLSX imports require an administrator.</p></section>;
  return <section className="imports-page"><div className="page-heading"><div><p className="eyebrow">Material library</p><h1>Import materials</h1></div></div>
    <article className="panel"><h2>Import AI results</h2><p>Upload the completed AI results JSON to review descriptions, tags and cited sources for existing materials before applying changes.</p>
      <button className="button button--primary" onClick={() => setAiOpen(true)}>Upload AI results JSON</button>
      {aiChanged && <p role="status">Reviewed AI results saved to the material library.</p>}
    </article>
    {aiOpen && <MaterialAiBriefDialog onClose={() => setAiOpen(false)} onChanged={() => setAiChanged(true)} />}
    {role === "ADMIN" ? <ImportWorkspace {...props} /> : <p>Historical CSV/XLSX imports require an administrator.</p>}
  </section>;
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
    const [orders, customers, processors] = await Promise.all([directoryClient.orders(), directoryClient.customers(), client.getInternalUsers()]);
    return { orders, customers, processors };
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
      // A later rejection cannot prove that an earlier lost response did not
      // commit. Keep the original packet until its receipt is recovered.
      if (confirming && (uncertain || !(cause instanceof ApiError) || cause.status >= 500)) {
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
      return !expected || Object.entries(expected).some(([key, value]) => key === "properties"
        ? JSON.stringify(Object.entries(row.properties ?? {}).sort()) !== JSON.stringify(Object.entries(value ?? {}).sort())
        : row[key as keyof typeof expected] !== value);
    })) throw new Error("The confirmed rows do not match the reviewed batch");
    confirmation.current = undefined; setUncertain(false); setResult(value); setHistoryVersion((value) => value + 1);
  }, true);
  const locked = pending || uncertain || Boolean(result);
  const mappedValues = inspection?.requiresSheet === false ? inspection.mappingValues : undefined;
  const mappingsComplete = mappedValues && groups.every((group) => mappedValues[group].every((label) =>
    Object.hasOwn(links[groupKeys[group]], label) && Boolean(links[groupKeys[group]][label])));
  return <section aria-label="Historical CSV and XLSX import"><h2>Import historical materials from CSV or XLSX</h2>
    <p>Keep historical identities and map each source label to an existing Customer, optional Order and Processor. An Order with an assigned Customer must match the material Customer. Imported materials require normal source checks and approval; imported Done and Checked values do not perform these checks.</p>
    {error && <p className="field-error" role="alert">{error}</p>}
    {pending && <p role="status">{confirming ? "Confirming import…" : "Checking import…"}</p>}
    {uncertain && <button className="button button--primary" disabled={pending} onClick={confirm}>Retry same import confirmation</button>}
    <article className="panel"><h2>1. Inspect source</h2>
      <a className="button" href={importTemplateUrl} download="material-import-template.csv">Download CSV template</a>
      <p>The template is UTF-8 CSV with a semicolon (;) delimiter. Replace the example row with your records, using existing Customer prefixes and unused historical numbers. Keep literal values when saving from Excel; for XLSX, choose a worksheet explicitly.</p>
      <details><summary>Template fields and accepted values</summary><ul>
        <li>Identity: PREFIX_0001_FULL-MATERIAL-NAME_B01. Use the real category code. The full product segment supplies the uppercase material name; Name is required for older three-part identities.</li>
        <li>Customer and Processor: your source labels, mapped to existing records in step 3. Order and Folder may be blank per row. Folder is relative to the library and must end with the exact identity.</li>
        <li>Color: six HEX digits, for example #AABBCC. Sample size: positive width × height in cm, for example 10x20-cm, with up to four decimal places.</li>
        <li>Done: YES or NO. Checked: no, OK or Correction. OK requires Done YES; Correction requires Done NO. Blanks mean In progress and no.</li>
        <li>Note: literal text, up to 2,048 characters per source cell. Brand identifier: optional customer identifier; blank leaves it unchanged. All nonblank values for one Customer must agree.</li>
      </ul><p>Map only the optional columns you want to import. Blank Color or Sample size means empty metadata. Description, tags, credits, collections, publication, approvals and automatic file-check results are not imported from CSV/XLSX. No folders or metadata files are created or changed.</p></details>
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
        }} />Import all rows without an Order column</label>
        <label><input type="checkbox" checked={columns.folder !== null} onChange={(event) => {
          setColumns({ ...columns, folder: event.target.checked ? "" : null }); setInspection({ ...inspection, mappingValues: undefined }); setLinks(blankLinks()); invalidatePreview();
        }} />Record existing folder references without creating or changing folders</label>
        <p>A blank cell in a mapped Order column leaves that row without an Order.</p>
        {columns.folder !== null && <p>Use paths relative to the configured material library; blank means no folder reference. References remain unverified until a source check succeeds.</p>}
        {fields.filter(({ key }) => (key !== "project" || columns.project !== null) && (key !== "folder" || columns.folder !== null)).map(({ key, label }) => <label key={key}>{label} column<select required value={columns[key] ?? ""} onChange={(event) => {
          setColumns({ ...columns, [key]: event.target.value }); setInspection({ ...inspection, mappingValues: undefined }); setLinks(blankLinks()); invalidatePreview();
        }}><option value="">Choose column</option>{inspection.headers.map((header) => <option key={header} disabled={Object.entries(columns).some(([field, value]) => field !== key && value === header)}>{header}</option>)}</select></label>)}
        <p>Optional spreadsheet properties. Done and Checked are historical values; importing them does not perform a technical source check. Nonblank Brand identifiers update the mapped Customers.</p>
        {propertyFields.map(({ key, label }) => <label key={key}>{label} column<select value={columns[key] ?? ""} onChange={(event) => {
          setColumns({ ...columns, [key]: event.target.value || null }); setInspection({ ...inspection, mappingValues: undefined }); setLinks(blankLinks()); invalidatePreview();
        }}><option value="">Do not import</option>{inspection.headers.map((header) => <option key={header} disabled={Object.entries(columns).some(([field, value]) => field !== key && value === header)}>{header}</option>)}</select></label>)}
        <button className="button" disabled={Object.values(columns).some((value) => value !== null && !value) || new Set(Object.values(columns).filter((value) => value !== null)).size !== Object.values(columns).filter((value) => value !== null).length}>Load source labels</button>
      </fieldset></form>
    </article>}
    {mappedValues && <article className="panel"><h2>3. Select existing records</h2>
      <p>Select existing Customers and optional Orders. The preview checks that each Order belongs to its material Customer; older Orders without a Customer remain usable.</p>
      {references.error ? <ErrorState message="Existing records could not be loaded." retry={references.retry} /> : !references.data ? <LoadingState label="Loading existing records…" /> :
        <form onSubmit={(event) => { event.preventDefault(); prepare(); }}><fieldset disabled={locked}><legend>Explicit source label mappings</legend>
          {groups.map((group) => <section key={group} aria-label={`${group} mappings`}><h3>{group === "project" ? "Orders" : group === "brand" ? "Customers" : "Processors"}</h3>
            {mappedValues[group].map((label) => <label key={label}>{groupLabels[group]}: {label}
              <select required value={links[groupKeys[group]][label] ?? ""} onChange={(event) => {
                setLinks({ ...links, [groupKeys[group]]: { ...links[groupKeys[group]], [label]: event.target.value } }); invalidatePreview();
              }}><option value="">Choose existing {groupLabels[group]}</option>
                {group === "project" ? references.data!.orders.map((item) => <option value={item.id} key={item.id}>{item.generatedName} · {item.number}</option>) :
                  group === "brand" ? references.data!.customers.filter((item) => item.isActive).map((item) => <option value={item.id} key={item.id}>{item.name} · {item.folderPrefix}</option>) :
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
