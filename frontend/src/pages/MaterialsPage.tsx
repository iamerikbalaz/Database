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
import { categoryLabel, materialCategories } from "../data/materialCategories";
import { GalleryStore } from "../api/galleryStore";
import { sessionGeneration } from "../auth/sessionTransport";
import { useSession } from "../auth/context";
import { OfflinePublicationWorkspace } from "../components/OfflinePublicationWorkspace";
import { MaterialBulkCheck } from "../components/MaterialBulkCheck";
import { requestNavigation } from "../navigationGuard";

const sizes: GallerySize[] = ["small", "medium", "large", "extra-large"];
function preference(key: string) { try { return localStorage.getItem(key); } catch { return null; } }
function savePreference(key: string, value: string) { try { localStorage.setItem(key, value); } catch { /* The view also works with browser storage disabled. */ } }

export function MaterialsPage({ client, navigate, initialView, archived = false }: { client: ApiClient; navigate: (path: string) => void; initialView?: "gallery"; archived?: boolean }) {
  const [tableBusy, setTableBusy] = useState(false);
  const [publicationSelection, setPublicationSelection] = useState<Material[] | null>(null);
  const [publicationBusy, setPublicationBusy] = useState(false);
  const [checkBusy, setCheckBusy] = useState(false);
  const role = useSession()?.session.user.role;
  const canPublish = !archived && (role === "ADMIN" || role === "PRODUCTION_LEAD");
  const canCheck = !archived && (role === "ADMIN" || role === "PRODUCTION_LEAD" || role === "PROCESSOR");
  const busy = tableBusy || checkBusy || publicationSelection !== null;
  const [filters, setFilters] = useState<MaterialFilters>({});
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
  const materials = result.data?.map(row => overrides[row.id] && overrides[row.id].updatedAt > row.updatedAt ? overrides[row.id] : row);
  const selectedMaterials = materials?.filter(row => selectedIds.has(row.id)) ?? [];
  const [lastResult, setLastResult] = useState(result.data);
  if (result.data && lastResult !== result.data) {
    setLastResult(result.data);
    const visible = new Set(result.data.map(row => row.id));
    setSelectedIds(new Set([...selectedIds].filter(id => visible.has(id))));
  }
  const changeFilters = (next: MaterialFilters) => { setSelectedIds(new Set()); setFilters(next); };
  const loadOptions = useCallback(() => Promise.all([
    client.getProjects(), client.getBrands(), client.getInternalUsers(),
  ]), [client]);
  const options = useResource(loadOptions);
  const [projects = [], brands = [], users = []] = options.data ?? [];
  const preparePublication = (materials: Material[]) => {
    if (tableBusy || checkBusy || !canPublish || !materials.length || materials.length > 100) return;
    setPublicationSelection(materials.map(material => ({ ...material })));
  };
  const selectors: { key: Exclude<keyof MaterialFilters, "color_hex">; label: string; options: { value: string; label: string }[] }[] = [
    { key: "project_id", label: "Order", options: projects.map((p) => ({ value: p.id, label: p.name })) },
    { key: "published_brand_id", label: "Customer", options: brands.map((b) => ({ value: b.id, label: b.name })) },
    { key: "assigned_processor_id", label: "Processor", options: users.map((u) => ({ value: u.id, label: u.displayName + (u.isActive ? "" : " (inactive)") })) },
    { key: "workflow_status", label: "Status", options: workflowStatuses.map((value) => ({ value, label: statusLabel(value) })) },
    { key: "checked_status", label: "Checked", options: checkedStatuses.map((value) => ({ value, label: value })) },
    { key: "automatic_file_check_status", label: "Automatic check", options: automaticFileCheckStatuses.map(value => ({ value, label: value === "NOT_CHECKED" ? "Not checked" : value === "ISSUES" ? "Issues" : "OK" })) },
    { key: "is_published", label: "Published", options: [{ value: "true", label: "Yes" }, { value: "false", label: "No" }] },
  ];
  return <section>
    <div className="page-heading"><div><p className="eyebrow">Production</p><h1>{archived ? "Archived materials" : "Materials"}</h1><p>{archived ? "Manage archived material records. Clear Archived to return a material to active work." : "Manage material records and their production status."}</p></div>
      <NavigationLink className="button button--primary" href="/materials/new" navigate={navigate}>Add material</NavigationLink>
    </div>
    <fieldset disabled={busy} className="panel material-filters" role="search" aria-label="Material filters">
      <label className="form-field">Search materials<input type="search" placeholder="Name, identity or note / #tag" value={filters.search ?? ""} onChange={(e) => changeFilters({ ...filters, search: e.target.value })} /></label>
      <label className="form-field">Main category<select value={filters.main_category_code ?? ""} onChange={(e) => changeFilters({ ...filters, main_category_code: e.target.value })}><option value="">All</option>{materialCategories.map(c => <option key={c.code} value={c.code}>{categoryLabel(c.code)}</option>)}{(result.data ?? []).filter((m, i, all) => !materialCategories.some(c => c.code === m.mainCategoryCode) && all.findIndex(x => x.mainCategoryCode === m.mainCategoryCode) === i).map(m => <option key={m.mainCategoryCode} value={m.mainCategoryCode}>{categoryLabel(m.mainCategoryCode)}</option>)}</select></label>
      <MaterialColorFilter value={filters.color_hex ?? []} onChange={colors => changeFilters({ ...filters, color_hex: colors })} disabled={busy} />
      {selectors.map((s) => <label className="form-field" key={s.key}>{s.label}
        <select value={filters[s.key] ?? ""} onChange={(e) => changeFilters({ ...filters, [s.key]: e.target.value })}>
          <option value="">All</option>{s.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>)}
      <button className="button" onClick={() => changeFilters({})}>Clear filters</button>
    </fieldset>
    {options.error && <div role="alert" className="form-error">Related names and filter options could not be loaded. IDs are shown instead. <button onClick={options.retry}>Retry related records</button></div>}
    <div className="materials-view-toolbar">
      <span className="materials-count" aria-live="polite">{result.data ? `${result.data.length} materials` : "Materials"}</span>
      <div className="materials-view-controls" role="group" aria-label="Material display">
        <button disabled={busy} className="button" aria-pressed={view === "list"} onClick={() => { setView("list"); savePreference("materials.view", "list"); }}>List</button>
        <button disabled={busy} className="button" aria-pressed={view === "gallery"} onClick={() => { setView("gallery"); savePreference("materials.view", "gallery"); }}>Gallery</button>
      </div>
      {view === "gallery" && <><label className="gallery-size-control">Preview size<select value={size} onChange={event => { setSize(event.target.value as GallerySize); savePreference("materials.gallerySize", event.target.value); }}>
        {sizes.map(value => <option key={value} value={value}>{value === "extra-large" ? "Extra large" : value[0].toUpperCase() + value.slice(1)}</option>)}
      </select></label><button className="button gallery-refresh" onClick={() => { store.clear(); setPreviewEpoch(value => value + 1); }}>Refresh previews</button></>}
    </div>
    {view === "gallery" && publicationSelection === null && materials && (role === "ADMIN" || role === "PRODUCTION_LEAD" || role === "PROCESSOR" || canPublish) && <div className="material-table-toolbar">
      <label><input type="checkbox" aria-label="Select all visible materials" disabled={busy} checked={materials.length > 0 && selectedMaterials.length === materials.length}
        onChange={event => setSelectedIds(new Set(event.target.checked ? materials.map(row => row.id) : []))} />Select all</label>
      <span>{selectedMaterials.length} selected</span>
      {canPublish && <button className="button" disabled={busy || !selectedMaterials.length || selectedMaterials.length > 100} onClick={() => preparePublication(selectedMaterials)}>Prepare selected for publication ({selectedMaterials.length})</button>}
    </div>}
    {canPublish && publicationSelection === null && selectedMaterials.length > 100 && <p>Each publication batch supports up to 100 materials. Select up to 100 rows.</p>}
    {canCheck && publicationSelection === null && <div className="material-table-toolbar"><MaterialBulkCheck materials={selectedMaterials}
      disabled={tableBusy || Boolean(result.error) || !result.data} onBusyChange={setCheckBusy} onChecked={result.retry} /></div>}
    {publicationSelection !== null ? <section aria-label="Material publication preparation">
      <div className="page-heading"><div><h2>Prepare publication</h2><p>Review materials, prepare ZIP files and export CSV for the library.</p></div>
        <button className="button" disabled={publicationBusy} onClick={() => { if (requestNavigation("/materials")) setPublicationSelection(null); }}>Back to material list</button>
      </div>
      <OfflinePublicationWorkspace client={client} navigate={navigate} initialSelection={publicationSelection} onBusyChange={setPublicationBusy} onChanged={result.retry} />
    </section> : result.error ? <ErrorState message={materialLoadError(result.cause)} retry={result.retry} />
      : !result.data ? <LoadingState label="Loading materials…" />
      : !result.data.length ? <EmptyState title="No materials found" description="Clear the filters or add a material to get started." />
      : view === "gallery" ? <MaterialsGrid key={`${generation}:${previewEpoch}`} materials={materials!} store={store} size={size} navigate={navigate}
          selection={role === "ADMIN" || role === "PRODUCTION_LEAD" || role === "PROCESSOR" || canPublish ? { ids: selectedIds, change: setSelectedIds } : undefined} disabled={busy} />
      : <MaterialsTable materials={materials!} store={store} client={client} projects={projects} brands={brands} users={users}
          navigate={navigate} refresh={result.retry} onBusyChange={setTableBusy} onPreparePublication={canPublish ? preparePublication : undefined}
          selection={{ ids: selectedIds, change: setSelectedIds }} operationBusy={checkBusy} onMaterialChanged={row => setOverrides(current => ({ ...current, [row.id]: row }))} />}
  </section>;
}
