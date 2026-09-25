import { useCallback, useEffect, useMemo, useState } from "react";
import type { ApiClient } from "../api/client";
import { materialLoadError, type MaterialFilters } from "../api/materialClient";
import { checkedStatuses, statusLabel, workflowStatuses } from "../api/materialDto";
import { useResource } from "../api/useResource";
import { NavigationLink } from "../components/NavigationLink";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { MaterialsGrid, type GallerySize } from "../components/MaterialsGrid";
import { MaterialsTable } from "../components/MaterialsTable";
import { categoryLabel, materialCategories } from "../data/materialCategories";
import { GalleryStore } from "../api/galleryStore";
import { sessionGeneration } from "../auth/sessionTransport";

const sizes: GallerySize[] = ["small", "medium", "large", "extra-large"];
function preference(key: string) { try { return localStorage.getItem(key); } catch { return null; } }
function savePreference(key: string, value: string) { try { localStorage.setItem(key, value); } catch { /* The view also works with browser storage disabled. */ } }

export function MaterialsPage({ client, navigate, initialView }: { client: ApiClient; navigate: (path: string) => void; initialView?: "gallery" }) {
  const [tableBusy, setTableBusy] = useState(false);
  const [filters, setFilters] = useState<MaterialFilters>({});
  const [view, setView] = useState<"list" | "gallery">(() => initialView ?? (preference("materials.view") === "gallery" ? "gallery" : "list"));
  const [size, setSize] = useState<GallerySize>(() => { const saved = preference("materials.gallerySize"); return sizes.find(value => value === saved) ?? "medium"; });
  const [previewEpoch, setPreviewEpoch] = useState(0);
  const generation = sessionGeneration();
  const store = useMemo(() => new GalleryStore(generation), [generation]);
  useEffect(() => () => store.clear(), [store]);
  const load = useCallback(() => client.getMaterials(filters), [client, filters]);
  const result = useResource(load);
  const loadOptions = useCallback(() => Promise.all([
    client.getProjects(), client.getBrands(), client.getInternalUsers(),
  ]), [client]);
  const options = useResource(loadOptions);
  const [projects = [], brands = [], users = []] = options.data ?? [];
  const selectors: { key: keyof MaterialFilters; label: string; options: { value: string; label: string }[] }[] = [
    { key: "project_id", label: "Project", options: projects.map((p) => ({ value: p.id, label: p.name })) },
    { key: "published_brand_id", label: "Published brand", options: brands.map((b) => ({ value: b.id, label: b.name })) },
    { key: "assigned_processor_id", label: "Processor", options: users.map((u) => ({ value: u.id, label: u.displayName + (u.isActive ? "" : " (inactive)") })) },
    { key: "workflow_status", label: "Status", options: workflowStatuses.map((value) => ({ value, label: statusLabel(value) })) },
    { key: "checked_status", label: "Checked", options: checkedStatuses.map((value) => ({ value, label: value })) },
    { key: "is_published", label: "Published", options: [{ value: "true", label: "Yes" }, { value: "false", label: "No" }] },
  ];
  return <section>
    <div className="page-heading"><div><p className="eyebrow">Production</p><h1>Materials</h1><p>Manage material records and their production status.</p></div>
      <NavigationLink className="button button--primary" href="/materials/new" navigate={navigate}>Add material</NavigationLink>
    </div>
    <fieldset disabled={tableBusy} className="panel material-filters" role="search" aria-label="Material filters">
      <label className="form-field">Search materials<input type="search" value={filters.search ?? ""} onChange={(e) => setFilters({ ...filters, search: e.target.value })} /></label>
      <label className="form-field">Main category<select value={filters.main_category_code ?? ""} onChange={(e) => setFilters({ ...filters, main_category_code: e.target.value })}><option value="">All</option>{materialCategories.map(c => <option key={c.code} value={c.code}>{categoryLabel(c.code)}</option>)}{(result.data ?? []).filter((m, i, all) => !materialCategories.some(c => c.code === m.mainCategoryCode) && all.findIndex(x => x.mainCategoryCode === m.mainCategoryCode) === i).map(m => <option key={m.mainCategoryCode} value={m.mainCategoryCode}>{categoryLabel(m.mainCategoryCode)}</option>)}</select></label>
      {selectors.map((s) => <label className="form-field" key={s.key}>{s.label}
        <select value={filters[s.key] ?? ""} onChange={(e) => setFilters({ ...filters, [s.key]: e.target.value })}>
          <option value="">All</option>{s.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>)}
      <button className="button" onClick={() => setFilters({})}>Clear filters</button>
    </fieldset>
    {options.error && <div role="alert" className="form-error">Related names and filter options could not be loaded. IDs are shown instead. <button onClick={options.retry}>Retry related records</button></div>}
    <div className="materials-view-toolbar">
      <span className="materials-count" aria-live="polite">{result.data ? `${result.data.length} materials` : "Materials"}</span>
      <div className="materials-view-controls" role="group" aria-label="Material display">
        <button disabled={tableBusy} className="button" aria-pressed={view === "list"} onClick={() => { setView("list"); savePreference("materials.view", "list"); }}>List</button>
        <button disabled={tableBusy} className="button" aria-pressed={view === "gallery"} onClick={() => { setView("gallery"); savePreference("materials.view", "gallery"); }}>Gallery</button>
      </div>
      {view === "gallery" && <><label className="gallery-size-control">Preview size<select value={size} onChange={event => { setSize(event.target.value as GallerySize); savePreference("materials.gallerySize", event.target.value); }}>
        {sizes.map(value => <option key={value} value={value}>{value === "extra-large" ? "Extra large" : value[0].toUpperCase() + value.slice(1)}</option>)}
      </select></label><button className="button gallery-refresh" onClick={() => { store.clear(); setPreviewEpoch(value => value + 1); }}>Refresh previews</button></>}
    </div>
    {result.error ? <ErrorState message={materialLoadError(result.cause)} retry={result.retry} />
      : !result.data ? <LoadingState label="Loading materials…" />
      : !result.data.length ? <EmptyState title="No materials found" description="Clear the filters or add a material to get started." />
      : view === "gallery" ? <MaterialsGrid key={`${generation}:${previewEpoch}`} materials={result.data} store={store} size={size} navigate={navigate} />
      : <MaterialsTable materials={result.data} store={store} client={client} projects={projects} brands={brands} users={users}
          navigate={navigate} refresh={result.retry} onBusyChange={setTableBusy} />}
  </section>;
}
