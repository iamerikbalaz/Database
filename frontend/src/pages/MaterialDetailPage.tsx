import { catalogMaterialCategories, catalogMaterialCategoryLabels, categoryLabel } from "../data/materialCategories";
import { catalogClient } from "../api/catalogClient";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ApiClient } from "../api/client";
import { materialLoadError } from "../api/materialClient";
import { validateFolderPath } from "../api/folderPathValidation";
import { useSession } from "../auth/context";
import { statusLabel, type Material } from "../api/materialDto";
import { GalleryStore } from "../api/galleryStore";
import { useResource } from "../api/useResource";
import { ErrorState, LoadingState } from "../components/PageState";
import { NavigationLink } from "../components/NavigationLink";
import { MaterialContentPanel } from "../components/MaterialContentPanel";
import { MaterialGallery } from "../components/MaterialGallery";
import { MaterialsTable } from "../components/MaterialsTable";
import { MaterialDataFolder, MaterialDataCheck } from "../components/MaterialDataFolder";
import { MaterialNameDialog } from "../components/MaterialNameDialog";
import { MaterialHistoryPanel } from "../components/MaterialHistoryPanel";
import "../components/MaterialLayout.css";

export function MaterialFacts({ material: m }: { material: Material }) {
  const visibleFolderPath = m.folderPath && validateFolderPath(m.folderPath).error === null ? m.folderPath : null;
  const fields = [
    ["Internal UUID", m.id], ["Technical identity", m.technicalIdentity ?? "Not assigned"],
    ["Sequence number", m.sequenceNumber === null ? "Not assigned" : String(m.sequenceNumber).padStart(4, "0")],
    ["Material name", m.materialName], ["Main category", m.mainCategoryCode ? categoryLabel(m.mainCategoryCode) : "Not assigned"],
    ["Folder status", m.folderPath ? "Linked" : "Not linked"],
    ["Folder path", visibleFolderPath ?? (m.folderPath ? "Unavailable (unsafe path hidden)" : "Not linked")],
    ["Status", statusLabel(m.workflowStatus)],
    ["Checked", m.checkedStatus], ["Note", m.note ?? "—"],
    ["Published", m.isPublished ? "Yes" : "No"], ["Created", m.createdAt], ["Updated", m.updatedAt],
  ];
  return <dl className="info-list material-facts">{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

function MaterialDetailContent({ initialMaterial, client, navigate, includeArchived }: {
  initialMaterial: Material; client: ApiClient; navigate: (path: string) => void; includeArchived: boolean;
}) {
  const role = useSession()?.session.user.role;
  const [material, setMaterial] = useState(initialMaterial);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState("");
  const [propertiesBusy, setPropertiesBusy] = useState(false);
  const [libraryBusy, setLibraryBusy] = useState(false);
  const [folderBusy, setFolderBusy] = useState(false), [checkBusy, setCheckBusy] = useState(false);
  const [checkActionTarget, setCheckActionTarget] = useState<HTMLDivElement | null>(null);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const store = useMemo(() => new GalleryStore(), []);
  useEffect(() => () => store.clear(), [store]);
  const loadOptions = useCallback(() => Promise.all([client.getProjects(), client.getBrands(), client.getInternalUsers()]), [client]);
  const options = useResource(loadOptions);
  const catalog = useResource(catalogClient.categories);
  const [projects = [], brands = [], users = []] = options.data ?? [];
  const refresh = useCallback(async (fallback?: Material) => {
    if (fallback) setMaterial(fallback);
    setRefreshing(true); setRefreshError("");
    try {
      const updated = await client.getMaterial(material.id, includeArchived || role === "ADMIN");
      setMaterial(updated);
      setHistoryRefresh(revision => revision + 1);
      if (updated.isArchived !== material.isArchived) navigate(updated.isArchived ? `/material-archives/${material.id}` : `/materials/${material.id}`);
      return true;
    }
    catch { setRefreshError("Change saved, but the material could not be reloaded. Refresh the material data."); return false; }
    finally { setRefreshing(false); }
  }, [client, material.id, material.isArchived, includeArchived, role, navigate]);
  const refreshFromProperties = useCallback(() => { void refresh(); }, [refresh]);
  const canEdit = (role === "ADMIN" || role === "PRODUCTION_LEAD") && !material.isArchived;
  const [editName, setEditName] = useState(false);
  return <section className="material-detail-page">
    <NavigationLink className="back-link" href={material.isArchived ? "/material-archives" : "/materials"} navigate={navigate}>{material.isArchived ? "Back to archived materials" : "Back to materials"}</NavigationLink>
    <div className="page-heading"><div className="material-page-identity"><p className="eyebrow">{material.technicalIdentity ?? "Identity not assigned"}</p><div className="material-name-heading"><h1>{material.materialName}</h1>
      {canEdit && <button className="button" disabled={refreshing || propertiesBusy || libraryBusy || folderBusy || checkBusy} onClick={() => setEditName(true)}>Edit Name</button>}</div></div>
      <div className="material-check-action" ref={setCheckActionTarget} />
    </div>
    <MaterialDataCheck key={material.id} materialId={material.id} updatedAt={material.updatedAt} actionTarget={checkActionTarget} dockOnly
      disabled={propertiesBusy || libraryBusy || folderBusy || refreshing || editName || !material.folderPath || !role || !["ADMIN", "PRODUCTION_LEAD", "PROCESSOR"].includes(role)} onChanged={refresh} onBusyChange={setCheckBusy} />
    <div className="material-detail-summary">
    {role && <MaterialGallery key={`gallery-${material.id}-${material.folderPath}-${material.updatedAt}`} materialId={material.id} linked={Boolean(material.folderPath)} />}
    <article className="panel material-basic-panel" aria-label="Material properties">
      {options.error && <p role="alert">Related property choices could not be loaded. <button onClick={options.retry}>Retry related records</button></p>}
      {catalog.error && <p role="alert">Category choices could not be loaded. The current category is retained. <button onClick={catalog.retry}>Retry categories</button></p>}
      <fieldset className="material-properties-fieldset" disabled={libraryBusy || folderBusy || checkBusy || editName}><legend>Material properties</legend><MaterialsTable detail materials={[material]} store={store} client={client} projects={projects} brands={brands} users={users}
        categories={catalogMaterialCategories(catalog.data ?? [])} categoryLabels={catalogMaterialCategoryLabels(catalog.data ?? [])}
        navigate={navigate} refresh={refreshFromProperties} onBusyChange={setPropertiesBusy} onMaterialChanged={setMaterial} />
      </fieldset>
    </article>
    </div>
    {refreshError && <div role="alert">{refreshError}<button className="button" disabled={libraryBusy} onClick={() => void refresh()}>Reload material data</button></div>}
    {role && <div className="material-detail-data">
      <MaterialDataFolder key={`folder-${material.id}-${material.folderPath}`} material={material} onChanged={refresh} onBusyChange={setFolderBusy} disabled={propertiesBusy || libraryBusy || refreshing || checkBusy || editName} />
      {!material.isArchived && <MaterialContentPanel key={`content-${material.id}-${material.publishedBrandId}`} material={material} onChanged={refresh} onBusyChange={setLibraryBusy} disabled={propertiesBusy || folderBusy || checkBusy || editName} />}
    </div>}
    <MaterialHistoryPanel id={material.id} updatedAt={material.updatedAt} refreshRevision={historyRefresh} />
    {editName && <MaterialNameDialog material={material} client={client} onClose={() => setEditName(false)} onChanged={refresh} />}
  </section>;
}

export function MaterialDetailPage({ id, client, navigate, includeArchived = false }: { id: string; client: ApiClient; navigate: (path: string) => void; includeArchived?: boolean }) {
  const actor = useSession()?.session.user.id;
  const load = useCallback(() => { void actor; return client.getMaterial(id, includeArchived); }, [id, client, includeArchived, actor]);
  const { data, error, cause, retry } = useResource(load);
  if (error) return <ErrorState message={materialLoadError(cause)} retry={retry} />;
  if (!data) return <LoadingState label="Loading material…" />;
  return <MaterialDetailContent key={`${actor}:${data.id}:${data.updatedAt}:${data.isArchived}`} initialMaterial={data} client={client} navigate={navigate} includeArchived={includeArchived} />;
}
