import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { catalogClient, type MaterialContent } from "../api/catalogClient";
import { metadataClient, type MetadataObservation } from "../api/metadataClient";
import type { Material } from "../api/materialDto";
import type { LocalPublicationPreview } from "../api/localPublicationClient";
import { GalleryStore } from "../api/galleryStore";
import { sessionGeneration } from "../auth/sessionTransport";
import { useSession } from "../auth/context";
import { useResource } from "../api/useResource";
import { DatabaseTableViewport } from "./DatabaseTableViewport";
import { MaterialThumbnail } from "./MaterialsGrid";
import { MaterialPreviewStrip } from "./MaterialPreviewStrip";
import { MaterialContentPanel } from "./MaterialContentPanel";
import { MaterialLibraryBulkEditor } from "./MaterialLibraryBulkEditor";
import { MaterialBulkContent } from "./MaterialBulkContent";
import { MaterialAiBriefDialog } from "./MaterialAiBriefDialog";
import { MaterialsTable } from "./MaterialsTable";
import { ColoredValue } from "./ColoredSelect";
import { AutomaticFileCheckStatus } from "./AutomaticFileCheckStatus";
import { materialCheckedColors, materialStatusColors } from "../data/choiceColors";
import { highlightMaterial, isInteractiveTarget, type HighlightState } from "./materialHighlight";
import { NavigationLink } from "./NavigationLink";
import { Icon } from "./Icon";

type Row = { material: Material; content: MaterialContent | null; source: MetadataObservation | null; error: boolean };
const optionalColumns = ["Description", "Tags", "Credits", "Color", "Sample size", "Categories", "Customer", "Status", "Checked", "Automatic check"] as const;
function LibraryDialog({ row, client, navigate, onClose, onChanged, onBusyChange }: {
  row: Row; client: ApiClient; navigate: (path: string) => void; onClose: () => void; onChanged: () => void; onBusyChange: (value: boolean) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false), [propertiesBusy, setPropertiesBusy] = useState(false), [material, setMaterial] = useState(row.material);
  const generation = sessionGeneration(), store = useMemo(() => new GalleryStore(generation), [generation]);
  const mounted = useRef(false);
  const loadOptions = useCallback(() => Promise.all([client.getProjects(), client.getBrands(), client.getInternalUsers()]), [client]);
  const options = useResource(loadOptions);
  useEffect(() => { mounted.current = true; dialog.current?.showModal(); onBusyChange(true); return () => { mounted.current = false; store.clear(); onBusyChange(false); }; }, [onBusyChange, store]);
  const refresh = async () => { const current = await client.getMaterial(material.id); if (mounted.current) setMaterial(current); onChanged(); };
  const close = () => { if (!busy && !propertiesBusy) onClose(); };
  return <dialog ref={dialog} className="publication-library-dialog" aria-label={`Publication data for ${material.materialName}`} onCancel={event => { event.preventDefault(); close(); }}>
    <header><h2>{material.materialName}</h2><button className="button" disabled={busy || propertiesBusy} onClick={close}>Close</button></header>
    <MaterialContentPanel material={material} onChanged={refresh} onBusyChange={setBusy} disabled={propertiesBusy} />
    <details><summary>Status and material properties</summary>{options.data ? <MaterialsTable detail materials={[material]} store={store} client={client} projects={options.data[0]} brands={options.data[1]} users={options.data[2]} navigate={navigate} refresh={() => void refresh()} onBusyChange={setPropertiesBusy} operationBusy={busy} onMaterialChanged={value => { setMaterial(value); onChanged(); }} /> : <p>{options.error ? "Related records could not be loaded." : "Loading material properties…"}{options.error && <button className="button" onClick={options.retry}>Retry</button>}</p>}</details>
  </dialog>;
}

export function PublicationMaterialsTable({ materials, client, navigate, preview, refreshKey, disabled, onBusyChange, onChanged }: {
  materials: Material[]; client: ApiClient; navigate: (path: string) => void; preview: LocalPublicationPreview | null;
  refreshKey: number; disabled: boolean; onBusyChange: (value: boolean) => void; onChanged: () => void;
}) {
  const [batch] = useState(() => materials.map(material => ({ ...material })));
  const [selection, setSelection] = useState(new Set<string>()), [search, setSearch] = useState(""), [findings, setFindings] = useState("all"), [sort, setSort] = useState("name-asc");
  const [highlight, setHighlight] = useState<HighlightState>({ ids: new Set(), anchor: null });
  const [shown, setShown] = useState<readonly string[]>(optionalColumns);
  const [expanded, setExpanded] = useState(false), [counts, setCounts] = useState<Record<string, number>>({});
  const [revision, setRevision] = useState(0), [bulkBusy, setBulkBusy] = useState(false), [contentBusy, setContentBusy] = useState(false), [detailBusy, setDetailBusy] = useState(false);
  const [detail, setDetail] = useState<Row | null>(null), [ai, setAi] = useState<Material[] | null>(null);
  const generation = sessionGeneration(), store = useMemo(() => new GalleryStore(generation), [generation]);
  useEffect(() => () => store.clear(), [store]);
  const role = useSession()?.session.user.role, editable = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const load = useCallback(async () => {
    void refreshKey; void revision;
    const result: Row[] = new Array(batch.length); let next = 0;
    await Promise.all(Array.from({ length: Math.min(4, batch.length) }, async () => {
      while (next < batch.length) {
        const index = next++, original = batch[index];
        const values = await Promise.allSettled([client.getMaterial(original.id), catalogClient.content(original.id), metadataClient.inspect(original.id)]);
        result[index] = { material: values[0].status === "fulfilled" ? values[0].value : original,
          content: values[1].status === "fulfilled" ? values[1].value : null,
          source: values[2].status === "fulfilled" ? values[2].value : null, error: values.some(value => value.status === "rejected") };
      }
    }));
    const customers = await client.getBrands();
    return { rows: result, customers };
  }, [batch, client, refreshKey, revision]);
  const resource = useResource(load);
  const localBusy = bulkBusy || contentBusy || detailBusy || ai !== null;
  const locked = disabled || localBusy;
  useEffect(() => { onBusyChange(localBusy); return () => onBusyChange(false); }, [localBusy, onBusyChange]);
  const changed = () => { setRevision(value => value + 1); onChanged(); };
  const count = useCallback((id: string, value: number) => setCounts(current => current[id] === value ? current : { ...current, [id]: value }), []);
  const requiredIssues = (row: Row): string[] => {
    const result: string[] = [], material = row.material, content = row.content, source = row.source;
    const customer = resource.data?.customers.find(item => item.id === material.publishedBrandId);
    if (row.error) result.push("Data could not be loaded");
    if (material.isDraft || !material.technicalIdentity) result.push("Complete the material identity");
    if (material.workflowStatus !== "DONE") result.push("Status must be Done");
    if (!material.folderPath) result.push("Link the material folder");
    if (!customer || !customer.isActive || !customer.brandIdentifier || customer.brandIdentifier.startsWith("unassigned-")) result.push("Set an active Customer and brand identifier");
    if (!content?.revision) result.push("Save library data");
    if (content?.credits === null) result.push("Set credits");
    if (!content?.categories.length) result.push("Choose a category");
    if (content && [...content.categories, ...content.collections].some(item => !item.active)) result.push("Inactive category or collection");
    if (content?.collections.some(item => item.brandId !== material.publishedBrandId)) result.push("Collection belongs to another Customer");
    if (!source?.available || !source.sha256 || !["VALID", "WARNING"].includes(source.sourceStatus) || source.active) result.push("Check and save source metadata");
    if (!source?.values.hex_color) result.push("Choose a color");
    if (!source?.values.width_cm || !source.values.height_cm) result.push("Set sample size");
    return result;
  };
  const severity = (row: Row) => {
    const reviewed = preview?.items.find(item => item.materialId === row.material.id);
    if (reviewed?.errors.length || requiredIssues(row).length) return "error";
    if (reviewed?.warnings.length || !row.content?.description || !row.content?.tags.length || row.material.checkedStatus === "Correction" || row.material.automaticFileCheckStatus === "ISSUES") return "warning";
    return "ready";
  };
  const rows = (resource.data?.rows ?? []).filter(row => (findings === "all" || (findings === "issues" ? severity(row) !== "ready" : severity(row) === findings)) &&
    [row.material.materialName, row.material.technicalIdentity, row.content?.description, row.content?.tags.join(" ")].join(" ").toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  rows.sort((a, b) => sort.startsWith("name") ? (sort === "name-asc" ? 1 : -1) * a.material.materialName.localeCompare(b.material.materialName) :
    sort === "number-desc" ? (b.material.sequenceNumber ?? 0) - (a.material.sequenceNumber ?? 0) : b.material.createdAt.localeCompare(a.material.createdAt));
  const selected = (resource.data?.rows ?? []).filter(row => selection.has(row.material.id) && !row.error).map(row => row.material);
  const selectableRows = rows.filter(row => !row.error);
  const highlighted = selectableRows.filter(row => highlight.ids.has(row.material.id));
  const previewWidth = expanded ? Math.min(1120, Math.max(250, ...Object.values(counts).map(value => value * 124))) : 90;
  const check = (id: string, value: boolean) => setSelection(current => { const next = new Set(current); if (value) next.add(id); else next.delete(id); return next; });
  const field = (row: Row, name: string) => {
    const material = row.material, content = row.content, values = row.source?.values;
    if (name === "Status") return <ColoredValue value={material.workflowStatus} colors={materialStatusColors} label={material.workflowStatus === "DONE" ? "Done" : "In progress"} />;
    if (name === "Checked") return <ColoredValue value={material.checkedStatus} colors={materialCheckedColors} />;
    if (name === "Automatic check") return <AutomaticFileCheckStatus material={material} />;
    if (name === "Description") return content?.description || "Missing description";
    if (name === "Tags") return content?.tags.join(", ") || "No tags";
    if (name === "Credits") return content?.credits ?? "Missing credits";
    if (name === "Color") return values?.hex_color ? <span className="publication-color"><i style={{ background: values.hex_color }} />{values.hex_color}</span> : "Missing color";
    if (name === "Sample size") return `${values?.width_cm ?? "—"} × ${values?.height_cm ?? "—"} cm`;
    if (name === "Categories") return content?.categories.map(item => `${item.abbreviation ? item.abbreviation + " · " : ""}${item.value}`).join(", ") || "Missing categories";
    const customer = resource.data?.customers.find(item => item.id === material.publishedBrandId);
    return customer ? <><span>{customer.name}</span><small>{customer.brandIdentifier}</small></> : "Missing customer";
  };
  return <section className="publication-materials" aria-label="Publication materials">
    <div className="publication-materials-filters">
      <label className="form-field database-search">Search batch<span className="database-search-input"><Icon name="search" size={16} /><input type="search" value={search} disabled={locked} onChange={event => setSearch(event.target.value)} placeholder="Name, description or tags" /></span></label>
      <label className="form-field">Show<select value={findings} disabled={locked} onChange={event => setFindings(event.target.value)}><option value="all">All materials</option><option value="issues">Needs attention</option><option value="error">Missing required data</option><option value="warning">Warnings</option></select></label>
      <label className="form-field">Sort<select value={sort} disabled={locked} onChange={event => setSort(event.target.value)}><option value="name-asc">Name A–Z</option><option value="name-desc">Name Z–A</option><option value="number-desc">Number: largest first</option><option value="created-desc">Created: newest first</option></select></label>
      <button className="button resource-table-refresh" aria-label="Refresh publication materials" title="Refresh publication materials" disabled={locked} onClick={() => { store.clear(); setRevision(value => value + 1); onChanged(); }}><Icon name="refresh" size={18} /></button>
    </div>
    <div className="publication-materials-tools">
      <button className="button" disabled={locked || !editable || !highlighted.length} onClick={() => setSelection(current => new Set([...current, ...highlighted.map(row => row.material.id)]))}>Select highlighted ({highlighted.length})</button><span>{selected.length} selected · {rows.length}/{batch.length} shown</span>
      <details><summary>Properties</summary><div className="publication-properties">{optionalColumns.map(name => <label key={name}><input type="checkbox" checked={shown.includes(name)} disabled={locked} onChange={event => setShown(value => event.target.checked ? [...value, name] : value.filter(item => item !== name))} />{name}</label>)}</div></details>
      <span className="publication-row-key"><i className="publication-row-key--error" />Required data missing <i className="publication-row-key--warning" />Review warning</span>
    </div>
    {editable && selected.length > 0 && <fieldset className="material-bulk-bar publication-bulk-bar"><legend>Apply to {selected.length} selected materials</legend>
      <MaterialLibraryBulkEditor includeWorkflow materials={selected} client={client} disabled={disabled || contentBusy || detailBusy || ai !== null} onBusyChange={setBulkBusy} onChanged={changed} />
      <div className="material-bulk-actions"><MaterialBulkContent materials={selected} actionTarget={null} disabled={disabled || bulkBusy || detailBusy || ai !== null} onChanged={changed} onBusyChange={setContentBusy} />
        <button className="button" disabled={locked} onClick={() => setAi(selected.map(item => ({ ...item })))}>AI descriptions</button></div>
    </fieldset>}
    {resource.error ? <p role="alert">Publication data could not be loaded. <button className="button" onClick={resource.retry}>Retry publication data</button></p> : !resource.data ? <p>Loading publication data…</p> : <DatabaseTableViewport label="Publication material results" scrollMode="contained" className="publication-material-table">
      <table><thead><tr><th><input type="checkbox" aria-label="Select all visible publication materials" disabled={locked || !editable || !selectableRows.length} checked={selectableRows.length > 0 && selectableRows.every(row => selection.has(row.material.id))} onChange={event => setSelection(current => { const next = new Set(current); selectableRows.forEach(row => event.target.checked ? next.add(row.material.id) : next.delete(row.material.id)); return next; })} /></th>
        <th style={{ minWidth: previewWidth }}><button className="preview-column-toggle" aria-label={expanded ? "Collapse publication previews" : "Expand publication previews"} aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>Preview<Icon name="arrow" size={12} /></button></th><th>Material</th>{optionalColumns.filter(name => shown.includes(name)).map(name => <th key={name}>{name}</th>)}</tr></thead>
        <tbody>{rows.map(row => <tr key={row.material.id} className={`publication-data-${severity(row)}${selection.has(row.material.id) ? " is-selected" : ""}${highlight.ids.has(row.material.id) ? " is-highlighted" : ""}`} aria-label={`Publication row ${row.material.materialName}`} aria-selected={highlight.ids.has(row.material.id)} tabIndex={0}
          onMouseDown={event => { if ((event.shiftKey || event.ctrlKey || event.metaKey) && !isInteractiveTarget(event.target)) event.preventDefault(); }}
          onClick={event => { if (!locked && !isInteractiveTarget(event.target)) setHighlight(current => highlightMaterial(current, row.material.id, rows.map(row => row.material.id), event)); }}
          onKeyDown={event => { if (!locked && event.target === event.currentTarget && event.key === " ") { event.preventDefault(); setHighlight(current => highlightMaterial(current, row.material.id, rows.map(row => row.material.id), event)); } }}>
          <td><input type="checkbox" aria-label={`Select publication material ${row.material.materialName}`} checked={selection.has(row.material.id)} disabled={locked || !editable || row.error} onChange={event => check(row.material.id, event.target.checked)} /></td>
          <td>{expanded ? <MaterialPreviewStrip key={`${row.material.id}:${revision}:${refreshKey}`} material={row.material} store={store} editable={false} onEdit={() => {}} onCount={count} /> : <MaterialThumbnail material={row.material} store={store} />}</td>
          <td className="publication-material-name"><NavigationLink href={`/materials/${row.material.id}`} navigate={navigate}>{row.material.materialName}</NavigationLink><small>{row.material.technicalIdentity ?? "Incomplete identity"}</small>
            {row.error ? <span>Data unavailable — refresh to retry.</span> : editable && <button className="button" disabled={locked} onClick={() => setDetail(row)}>Edit library data</button>}
            {requiredIssues(row).length > 0 && <small className="form-error">{requiredIssues(row).join(" · ")}</small>}
            {preview?.items.find(item => item.materialId === row.material.id)?.errors.length ? <small>Required data missing — see review below.</small> : null}</td>
          {optionalColumns.filter(name => shown.includes(name)).map(name => <td key={name} className={name === "Description" ? "publication-description-cell" : ""}>{field(row, name)}</td>)}
        </tr>)}</tbody>
      </table>{rows.length === 0 && <p>No materials match these filters.</p>}
    </DatabaseTableViewport>}
    <p className="muted">Edits apply to the checked rows. Publication always includes all {batch.length} materials in this batch. Run Review materials again after changes to verify files and required values.</p>
    {detail && <LibraryDialog row={detail} client={client} navigate={navigate} onClose={() => setDetail(null)} onChanged={changed} onBusyChange={setDetailBusy} />}
    {ai && <MaterialAiBriefDialog materials={ai} onClose={() => setAi(null)} onChanged={changed} />}
  </section>;
}
