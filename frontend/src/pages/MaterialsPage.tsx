import { useCallback, useEffect, useMemo, useState } from "react";
import type { ApiClient } from "../api/client";
import { materialLoadError, type MaterialFilters } from "../api/materialClient";
import { automaticFileCheckStatuses, checkedStatuses, statusLabel, workflowStatuses, type Material } from "../api/materialDto";
import { useResource } from "../api/useResource";
import { NavigationLink } from "../components/NavigationLink";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { MaterialsGrid, type GallerySize } from "../components/MaterialsGrid";
import { MaterialsTable } from "../components/MaterialsTable";
import { MaterialColorFilter } from "../components/MaterialColorFilter";
import { catalogMaterialCategories, catalogMaterialCategoryLabels, categoryLabel } from "../data/materialCategories";
import { catalogClient } from "../api/catalogClient";
import { GalleryStore } from "../api/galleryStore";
import { sessionGeneration } from "../auth/sessionTransport";
import { useSession } from "../auth/context";
import { OfflinePublicationWorkspace } from "../components/OfflinePublicationWorkspace";
import { MaterialBulkCheck } from "../components/MaterialBulkCheck";
import { MaterialBulkContent } from "../components/MaterialBulkContent";
import { requestNavigation } from "../navigationGuard";
import { Icon } from "../components/Icon";
import { useDatabaseWorkspace } from "../components/useDatabaseWorkspace";
import { KeepFiltersControl, ResponsiveFilters } from "../components/ResponsiveFilters";
import { numberedDatabaseSortOptions, sortDatabaseRecords, useDatabaseFilters } from "../components/useDatabaseFilters";
import { DatabaseResultsToolbar } from "../components/DatabaseResultsToolbar";
import { PreviewEditDialog, type PreviewEditSelection } from "../components/PreviewEditDialog";
import { MaterialBulkNamesDialog } from "../components/MaterialBulkNamesDialog";
import { MaterialDeleteDialog } from "../components/MaterialDeleteDialog";
import { MaterialAiBriefDialog } from "../components/MaterialAiBriefDialog";
import { ColoredSelect } from "../components/ColoredSelect";
import { materialCheckedColors, materialStatusColors } from "../data/choiceColors";

const sizes: GallerySize[] = ["small", "medium", "large", "extra-large"];
const filterDefaults = { search: "", main_category_code: "", published_brand_id: "", project_id: "", workflow_status: "", assigned_processor_id: "", checked_status: "", is_published: "", automatic_file_check_status: "", color_hex: [] as string[], sort: "created-desc" };
function preference(key: string) { try { return localStorage.getItem(key); } catch { return null; } }
function savePreference(key: string, value: string) { try { localStorage.setItem(key, value); } catch { /* The view also works with browser storage disabled. */ } }

export function MaterialsPage({ client, navigate, initialView, archived = false }: { client: ApiClient; navigate: (path: string) => void; initialView?: "gallery"; archived?: boolean }) {
  const [tableBusy, setTableBusy] = useState(false);
  const [publicationSelection, setPublicationSelection] = useState<Material[] | null>(null);
  const compact = useDatabaseWorkspace() && publicationSelection === null;
  const [publicationBusy, setPublicationBusy] = useState(false);
  const [checkBusy, setCheckBusy] = useState(false);
  const [contentBusy, setContentBusy] = useState(false);
  const [previewSelection, setPreviewSelection] = useState<PreviewEditSelection | null>(null);
  const [nameSelection, setNameSelection] = useState<Material[] | null>(null);
  const [deleteSelection, setDeleteSelection] = useState<Material[] | null>(null);
  const [aiSelection, setAiSelection] = useState<Material[] | null>(null);
  const [checkActionTarget, setCheckActionTarget] = useState<HTMLDivElement | null>(null);
  const role = useSession()?.session.user.role;
  const canPublish = !archived && (role === "ADMIN" || role === "PRODUCTION_LEAD");
  const canCheck = !archived && (role === "ADMIN" || role === "PRODUCTION_LEAD" || role === "PROCESSOR");
  const canSelect = role === "ADMIN" || role === "PRODUCTION_LEAD" || role === "PROCESSOR";
  const busy = tableBusy || checkBusy || contentBusy || publicationSelection !== null || previewSelection !== null || nameSelection !== null || deleteSelection !== null || aiSelection !== null;
  const { filters: savedFilters, setFilters: setSavedFilters, keepFilters, setKeepFilters } = useDatabaseFilters(archived ? "material-archives" : "materials", filterDefaults);
  const queryKey = JSON.stringify(Object.fromEntries(Object.entries(savedFilters).filter(([key, value]) => key !== "sort" && (Array.isArray(value) ? value.length > 0 : Boolean(value)))));
  const filters = useMemo<MaterialFilters>(() => JSON.parse(queryKey), [queryKey]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [overrides, setOverrides] = useState<Record<string, Material>>({});
  const [view, setView] = useState<"list" | "gallery">(() => initialView ?? (preference("materials.view") === "gallery" ? "gallery" : "list"));
  const [size, setSize] = useState<GallerySize>(() => { const saved = preference("materials.gallerySize"); return sizes.find(value => value === saved) ?? "medium"; });
  const [previewEpoch, setPreviewEpoch] = useState(0);
  const generation = sessionGeneration();
  const store = useMemo(() => new GalleryStore(generation), [generation]);
  useEffect(() => () => store.clear(), [store]);
  const load = useCallback(() => client.getMaterials(archived ? { ...filters, is_archived: "true" } : filters), [client, filters, archived]);
  const result = useResource(load);
  const materials = result.data ? sortDatabaseRecords(result.data.map(row => overrides[row.id] && overrides[row.id].updatedAt > row.updatedAt ? overrides[row.id] : row), savedFilters.sort, row => row.materialName, row => row.createdAt, row => row.sequenceNumber ?? 0) : undefined;
  const selectedMaterials = materials?.filter(row => selectedIds.has(row.id)) ?? [];
  const [lastResult, setLastResult] = useState(result.data);
  if (result.data && lastResult !== result.data) {
    setLastResult(result.data);
    const visible = new Set(result.data.map(row => row.id));
    setSelectedIds(new Set([...selectedIds].filter(id => visible.has(id))));
  }
  const changeFilters = (next: MaterialFilters) => { setSelectedIds(new Set()); setSavedFilters(current => ({ ...filterDefaults, ...next, sort: current.sort })); };
  const loadOptions = useCallback(() => Promise.all([
    client.getProjects(), client.getBrands(), client.getInternalUsers(),
  ]), [client]);
  const options = useResource(loadOptions);
  const catalog = useResource(catalogClient.categories);
  const categories = catalogMaterialCategories(catalog.data ?? []);
  const categoryLabels = catalogMaterialCategoryLabels(catalog.data ?? []);
  const categoryCodes = [...new Set([...(result.data ?? []).map(row => row.mainCategoryCode).filter((code): code is string => code !== null), ...(filters.main_category_code ? [filters.main_category_code] : [])])];
  const [projects = [], brands = [], users = []] = options.data ?? [];
  const preparePublication = (materials: Material[]) => {
    if (tableBusy || checkBusy || !canPublish || !materials.length || materials.length > 100) return;
    setPublicationSelection(materials.map(material => ({ ...material })));
  };
  const prepareDeletion = (materials: Material[]) => {
    if (busy || role !== "ADMIN" || !materials.length || materials.length > 100) return;
    setDeleteSelection(materials.map(material => ({ ...material })));
  };
  const prepareAiBrief = (materials: Material[]) => {
    if (busy || !canPublish || !materials.length || materials.length > 100) return;
    setAiSelection(materials.map(material => ({ ...material })));
  };
  const selectors: { key: Exclude<keyof MaterialFilters, "color_hex">; label: string; options: { value: string; label: string }[] }[] = [
    { key: "published_brand_id", label: "Customer", options: brands.map((b) => ({ value: b.id, label: b.name })) },
    { key: "project_id", label: "Order", options: projects.map((p) => ({ value: p.id, label: p.name })) },
    { key: "workflow_status", label: "Status", options: workflowStatuses.map((value) => ({ value, label: statusLabel(value) })) },
    { key: "assigned_processor_id", label: "Processor", options: users.filter(u => u.role === "PROCESSOR" && (u.isActive || materials?.some(material => material.assignedProcessorId === u.id) || filters.assigned_processor_id === u.id)).map((u) => ({ value: u.id, label: u.displayName + (u.isActive ? "" : " (inactive)") })) },
    { key: "checked_status", label: "Checked", options: checkedStatuses.map((value) => ({ value, label: value })) },
    { key: "is_published", label: "Published", options: [{ value: "true", label: "Yes" }, { value: "false", label: "No" }] },
    { key: "automatic_file_check_status", label: "Automatic check", options: automaticFileCheckStatuses.map(value => ({ value, label: value === "NOT_CHECKED" ? "Not checked" : value === "ISSUES" ? "Issues" : "OK" })) },
  ];
  return <section className={`database-page materials-page${compact ? " database-page--workspace" : ""}`}>
    <div className="page-heading"><div><p className="eyebrow">Production</p><h1>{archived ? "Archived materials" : "Materials"}</h1><p className="database-description">{archived ? "Manage archived material records. Clear Archived to return a material to active work." : "Manage material records and their production status."}</p></div>
      <div className="database-heading-actions"><NavigationLink className="button button--primary" href="/materials/new" navigate={navigate}>Add material</NavigationLink><KeepFiltersControl checked={keepFilters} onChange={setKeepFilters} disabled={busy} /></div>
    </div>
    <ResponsiveFilters disabled={busy} compact={compact} label="Material filters" search onClear={() => changeFilters({})} filters={[
      { key: "search", width: 190, active: Boolean(filters.search), content: <label className="form-field database-search">Search materials<span className="database-search-input"><Icon name="search" size={18} /><input type="search" placeholder="Name, identity or note / #tag" value={filters.search ?? ""} onChange={(e) => changeFilters({ ...filters, search: e.target.value })} /></span></label> },
      { key: "main_category_code", width: 140, active: Boolean(filters.main_category_code), content: <label className="form-field">Main category<select value={filters.main_category_code ?? ""} onChange={(e) => changeFilters({ ...filters, main_category_code: e.target.value })}><option value="">All</option>{categories.map(c => <option key={c.code} value={c.code}>{categoryLabel(c.code, categories)}</option>)}{categoryCodes.filter(code => !categories.some(category => category.code === code || (category.aliases?.includes(code) && code !== filters.main_category_code))).map(code => <option key={code} value={code}>{categoryLabel(code, categories)}</option>)}</select></label> },
      ...selectors.map((s) => ({ key: s.key, width: s.key === "is_published" || s.key === "checked_status" ? 95 : s.key === "workflow_status" ? 100 : s.key === "project_id" || s.key === "published_brand_id" ? 130 : 125, active: Boolean(filters[s.key]), content: <label className="form-field">{s.label}
        <ColoredSelect value={filters[s.key] ?? ""} onChange={(e) => changeFilters({ ...filters, [s.key]: e.target.value })}
          colors={s.key === "workflow_status" ? materialStatusColors : s.key === "checked_status" ? materialCheckedColors : undefined}
          options={[{ value: "", label: "All" }, ...s.options]} />
      </label> })),
      { key: "color_hex", width: 140, active: Boolean(filters.color_hex?.length), content: <MaterialColorFilter value={filters.color_hex ?? []} onChange={colors => changeFilters({ ...filters, color_hex: colors })} disabled={busy} /> },
    ]} />
    {options.error && <div role="alert" className="form-error">Related names and filter options could not be loaded. IDs are shown instead. <button onClick={options.retry}>Retry related records</button></div>}
    {catalog.error && <div role="alert" className="form-error">Category options could not be loaded. Existing material categories are retained. <button onClick={catalog.retry}>Retry categories</button></div>}
    <DatabaseResultsToolbar count={result.data ? `${result.data.length} materials` : "Materials"} sort={savedFilters.sort}
      sortLabel="Sort materials" options={numberedDatabaseSortOptions} disabled={busy} onSortChange={sort => setSavedFilters(current => ({ ...current, sort }))}>
      <div className="materials-view-controls" role="group" aria-label="Material display">
        <button disabled={busy} className="button" aria-pressed={view === "list"} onClick={() => { setView("list"); savePreference("materials.view", "list"); }}>List</button>
        <button disabled={busy} className="button" aria-pressed={view === "gallery"} onClick={() => { setView("gallery"); savePreference("materials.view", "gallery"); }}>Gallery</button>
      </div>
      {view === "gallery" && <><label className="gallery-size-control">Preview size<select disabled={busy} value={size} onChange={event => { setSize(event.target.value as GallerySize); savePreference("materials.gallerySize", event.target.value); }}>
        {sizes.map(value => <option key={value} value={value}>{value === "extra-large" ? "Extra large" : value[0].toUpperCase() + value.slice(1)}</option>)}
      </select></label></>}
    </DatabaseResultsToolbar>
    {view === "gallery" && publicationSelection === null && materials && <div className="material-table-toolbar resource-table-toolbar">
      {canSelect && <><label><input type="checkbox" aria-label="Select all visible materials" disabled={busy} checked={materials.length > 0 && selectedMaterials.length === materials.length}
        onChange={event => setSelectedIds(new Set(event.target.checked ? materials.map(row => row.id) : []))} />Select all</label>
      <span>{selectedMaterials.length} selected</span></>}
      <div className="materials-table-actions">
      <button className="button resource-table-refresh gallery-refresh" aria-label="Refresh previews" title="Refresh previews" disabled={busy} onClick={() => { store.clear(); setPreviewEpoch(value => value + 1); }}><Icon name="refresh" size={20} /></button>
      </div>
    </div>}
    {view === "gallery" && (canCheck || role === "ADMIN") && publicationSelection === null && selectedMaterials.length > 0 && <fieldset className="material-bulk-bar"><legend>Apply to {selectedMaterials.length} selected materials</legend>
      <div className="material-bulk-actions">{canPublish && <button className="button" disabled={busy || selectedMaterials.length > 100} onClick={() => setNameSelection(selectedMaterials.map(item => ({ ...item })))}>Edit names</button>}{canCheck && <button className="button" disabled={busy || selectedMaterials.length > 100} onClick={() => setPreviewSelection({ materials: selectedMaterials.map(item => ({ ...item })), action: "BULK" })}>Edit previews</button>}
        {canCheck && <div className="material-check-actions-slot" ref={setCheckActionTarget} />}
        {canPublish && <button className="button" disabled={busy || selectedMaterials.length > 100} onClick={() => prepareAiBrief(selectedMaterials)}>AI descriptions</button>}
        {canPublish && <button className="button" disabled={busy || selectedMaterials.length > 100} onClick={() => preparePublication(selectedMaterials)}>Prepare selected for publication ({selectedMaterials.length})</button>}
        {role === "ADMIN" && <button className="button button--icon material-delete-trigger" aria-label="Delete selected materials" title="Delete selected materials" disabled={busy || selectedMaterials.length > 100} onClick={() => prepareDeletion(selectedMaterials)}><Icon name="trash" size={18} /></button>}
      </div>
    </fieldset>}
    {canPublish && publicationSelection === null && selectedMaterials.length > 100 && <p>Each publication batch supports up to 100 materials. Select up to 100 rows.</p>}
    {canCheck && publicationSelection === null && <MaterialBulkContent materials={selectedMaterials} actionTarget={checkActionTarget} dockOnly
      disabled={tableBusy || checkBusy || contentBusy || previewSelection !== null || nameSelection !== null || deleteSelection !== null || aiSelection !== null || Boolean(result.error) || !result.data} onBusyChange={setContentBusy} onChanged={result.retry} />}
    {canCheck && publicationSelection === null && <div className="material-table-toolbar materials-check-controls"><MaterialBulkCheck materials={selectedMaterials}
      actionTarget={checkActionTarget} dockOnly compact={compact} disabled={tableBusy || contentBusy || previewSelection !== null || nameSelection !== null || deleteSelection !== null || aiSelection !== null || Boolean(result.error) || !result.data} onBusyChange={setCheckBusy} onChecked={result.retry} /></div>}
    {publicationSelection !== null ? <section aria-label="Material publication preparation">
      <div className="page-heading"><div><h2>Prepare publication</h2><p>Review materials, prepare ZIP files and export CSV for the library.</p></div>
        <button className="button" disabled={publicationBusy} onClick={() => { if (requestNavigation("/materials")) setPublicationSelection(null); }}>Back to material list</button>
      </div>
      <OfflinePublicationWorkspace client={client} navigate={navigate} initialSelection={publicationSelection} onBusyChange={setPublicationBusy} onChanged={result.retry} />
    </section> : result.error ? <ErrorState message={materialLoadError(result.cause)} retry={result.retry} />
      : !result.data ? <LoadingState label="Loading materials…" />
      : !result.data.length ? <EmptyState title="No materials found" description="Clear the filters or add a material to get started." />
      : view === "gallery" ? <div className="database-scroll-region materials-gallery-scroll" role="region" aria-label="Material gallery results" tabIndex={0}><MaterialsGrid key={`${generation}:${previewEpoch}`} materials={materials!} store={store} size={size} navigate={navigate}
          selection={canSelect ? { ids: selectedIds, change: setSelectedIds } : undefined} disabled={busy} /></div>
      : <MaterialsTable materials={materials!} store={store} client={client} projects={projects} brands={brands} users={users}
          categories={categories} categoryLabels={categoryLabels}
          scrollMode={compact ? "contained" : "page"}
          checkActionsRef={canCheck ? setCheckActionTarget : undefined}
          navigate={navigate} refresh={result.retry} onBusyChange={setTableBusy} onPreparePublication={canPublish ? preparePublication : undefined}
          onDeleteSelected={role === "ADMIN" ? prepareDeletion : undefined}
          onAiBriefSelected={canPublish ? prepareAiBrief : undefined}
          selection={{ ids: selectedIds, change: setSelectedIds }} operationBusy={checkBusy || contentBusy || deleteSelection !== null || aiSelection !== null} onMaterialChanged={row => setOverrides(current => ({ ...current, [row.id]: row }))} />}
    {previewSelection && <PreviewEditDialog selection={previewSelection} onClose={() => setPreviewSelection(null)} onChanged={() => { previewSelection.materials.forEach(item => store.forget(item.id, item.folderPath ?? "")); setPreviewEpoch(value => value + 1); result.retry(); }} />}
    {nameSelection && <MaterialBulkNamesDialog materials={nameSelection} client={client} onClose={() => setNameSelection(null)} onChanged={() => { nameSelection.forEach(item => store.forget(item.id, item.folderPath ?? "")); setPreviewEpoch(value => value + 1); result.retry(); }} />}
    {deleteSelection && <MaterialDeleteDialog materials={deleteSelection} onClose={() => setDeleteSelection(null)} onFinished={() => { store.clear(); setOverrides({}); setSelectedIds(new Set()); setPreviewEpoch(value => value + 1); result.retry(); }} />}
    {aiSelection && <MaterialAiBriefDialog materials={aiSelection} onClose={() => setAiSelection(null)} onChanged={() => { setOverrides({}); result.retry(); }} />}
  </section>;
}
