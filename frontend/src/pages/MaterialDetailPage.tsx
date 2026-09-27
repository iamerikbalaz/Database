import { categoryLabel } from "../data/materialCategories";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import type { ApiClient } from "../api/client";
import { materialLoadError, materialOperationError } from "../api/materialClient";
import { validateFolderPath } from "../api/folderPathValidation";
import { useSession } from "../auth/context";
import { statusLabel, type Material } from "../api/materialDto";
import type { MaterialFinding, MaterialFolderPreflight } from "../api/materialOperationsDto";
import { GalleryStore } from "../api/galleryStore";
import { useResource } from "../api/useResource";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorState, LoadingState } from "../components/PageState";
import { NavigationLink } from "../components/NavigationLink";
import { MaterialContentPanel } from "../components/MaterialContentPanel";
import { MaterialMetadataEditor } from "../components/MaterialMetadataEditor";
import { MaterialGallery } from "../components/MaterialGallery";
import { MaterialsTable } from "../components/MaterialsTable";
import { FolderDiscovery } from "../components/FolderDiscovery";
import { MaterialFolderContents } from "../components/MaterialFolderContents";
import { PackagingJobsPanel } from "../components/PackagingJobsPanel";
import { ResourceHistoryPanel } from "../components/ResourceHistoryPanel";

type SavedOperation = "link";
function Value({ children }: { children: ReactNode }) {
  return children === null || children === undefined || children === "" ? (
    <span className="muted">Not available</span>
  ) : (
    <>{children}</>
  );
}

function safeFolderPath(path: string | null): string | null {
  if (path === null) return null;
  const validation = validateFolderPath(path);
  return validation.error === null ? validation.folderPath : null;
}

function ActionError({ message }: { message: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => ref.current?.focus(), [message]);
  return (
    <div ref={ref} className="form-error" role="alert" tabIndex={-1}>
      <strong>Action could not be completed</strong>
      <p>{message}</p>
    </div>
  );
}

function FindingList({ title, items }: { title: string; items: MaterialFinding[] }) {
  return (
    <div className="findings">
      <h3>{title}</h3>
      {items.length === 0 ? <p className="muted">None</p> : (
        <ul>{items.map((item, index) => (
          <li key={`${item.code}-${index}`}>
            <strong>{item.code}</strong>: {item.message}
            {item.path && <span> Path: {item.path}</span>}
            {item.expectedTechnicalIdentity && <span> Expected: {item.expectedTechnicalIdentity}.</span>}
            {item.actualFolderName && <span> Found: {item.actualFolderName}.</span>}
          </li>
        ))}</ul>
      )}
    </div>
  );
}

export function MaterialFacts({ material: m }: { material: Material }) {
  const visibleFolderPath = safeFolderPath(m.folderPath);
  const fields = [
    ["Internal UUID", m.id], ["Technical identity", m.technicalIdentity],
    ["Sequence number", String(m.sequenceNumber).padStart(4, "0")],
    ["Material name", m.materialName], ["Main category", categoryLabel(m.mainCategoryCode)],
    ["Folder status", m.folderPath ? "Linked" : "Not linked"],
    ["Folder path", visibleFolderPath ?? (m.folderPath ? "Unavailable (unsafe path hidden)" : "Not linked")],
    ["Status", statusLabel(m.workflowStatus)],
    ["Checked", m.checkedStatus], ["Note", m.note ?? "—"],
    ["Published", m.isPublished ? "Yes" : "No"], ["Created", m.createdAt], ["Updated", m.updatedAt],
  ];
  return <dl className="info-list material-facts">{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

function PreflightResult({ result, expectedIdentity }: { result: MaterialFolderPreflight; expectedIdentity: string }) {
  return <div className="preflight-result" aria-label="Folder check result">
    <h3>Folder check result</h3>
    <dl className="info-list">
      <div><dt>Folder name</dt><dd>{result.folderName}</dd></div>
      <div><dt>Expected identity</dt><dd>{expectedIdentity}</dd></div>
      <div><dt>Identity matches</dt><dd>{result.identityMatches ? "Yes" : "No"}</dd></div>
      <div><dt>Can continue</dt><dd>{result.canContinue ? "Yes" : "No"}</dd></div>
      <div><dt>Metadata status</dt><dd>{statusLabel(result.metadataStatus)}</dd></div>
      <div><dt>Source filename</dt><dd><Value>{result.sourceFilename}</Value></dd></div>
      <div><dt>HEX color</dt><dd><Value>{result.hexColor}</Value></dd></div>
      <div><dt>Real size</dt><dd>{result.widthCm && result.heightCm ? `${result.widthCm} × ${result.heightCm} cm` : <Value>{null}</Value>}</dd></div>
      <div><dt>Master resolution</dt><dd><Value>{result.masterResolution}</Value></dd></div>
      <div><dt>ZIP policy</dt><dd><Value>{result.policy}</Value></dd></div>
      <div><dt>Source checksum</dt><dd className="checksum"><Value>{result.sha256}</Value></dd></div>
    </dl>
    <FindingList title="Warnings" items={result.warnings} />
    <FindingList title="Errors" items={result.errors} />
  </div>;
}

function FolderControls({
  material,
  client,
  refreshing,
  onSaved,
}: {
  material: Material;
  client: ApiClient;
  refreshing: boolean;
  onSaved: (fallback: Material, operation: SavedOperation) => Promise<boolean>;
}) {
  const [folderPath, setFolderPath] = useState(safeFolderPath(material.folderPath) ?? "");
  const [checkedPath, setCheckedPath] = useState("");
  const [preflight, setPreflight] = useState<MaterialFolderPreflight>();
  const [checking, setChecking] = useState(false);
  const [linking, setLinking] = useState(false);
  const [pathError, setPathError] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const pendingCheck = useRef(false);
  const pendingLink = useRef(false);
  const folderInput = useRef<HTMLInputElement>(null);
  const linkButton = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const restoreDialogFocus = useRef(true);

  const checkFolder = async (event: FormEvent) => {
    event.preventDefault();
    if (pendingCheck.current) return;
    const validation = validateFolderPath(folderPath);
    setFolderPath(validation.folderPath);
    if (validation.error) {
      setPathError(validation.error);
      setPreflight(undefined);
      setCheckedPath("");
      setError("");
      setNotice("");
      folderInput.current?.focus();
      return;
    }
    const path = validation.folderPath;
    pendingCheck.current = true;
    setChecking(true);
    setPathError("");
    setError("");
    setNotice("");
    setPreflight(undefined);
    try {
      const result = await client.preflightMaterialFolder(material.id, path);
      setCheckedPath(path);
      setPreflight(result);
      setNotice(material.workflowStatus === "DONE" && path !== material.folderPath
        ? "Reopen this material before connecting a different folder."
        : result.canContinue && result.identityMatches
        ? "Folder check passed. The folder can be linked."
        : "Folder check finished. Review the findings before continuing.");
    } catch (cause) {
      setError(materialOperationError(cause, "preflight"));
    } finally {
      pendingCheck.current = false;
      setChecking(false);
    }
  };

  const canLink = Boolean(
    preflight && preflight.identityMatches && preflight.canContinue &&
    checkedPath === folderPath && (material.workflowStatus !== "DONE" || checkedPath === material.folderPath),
  );

  const linkFolder = async () => {
    if (!canLink || pendingLink.current) return;
    pendingLink.current = true;
    setLinking(true);
    setError("");
    setNotice("");
    try {
      const result = await client.linkMaterialFolder(material.id, checkedPath);
      const linkedPath = safeFolderPath(result.material.folderPath) ?? checkedPath;
      setFolderPath(linkedPath);
      setPreflight(undefined);
      setCheckedPath("");
      dialog.current?.close();
      const refreshed = await onSaved(result.material, "link");
      setNotice(refreshed ? `Folder “${linkedPath}” was linked successfully.` : "");
    } catch (cause) {
      setError(materialOperationError(cause, "link"));
      restoreDialogFocus.current = false;
      dialog.current?.close();
    } finally {
      pendingLink.current = false;
      setLinking(false);
    }
  };

  return <article className="panel panel--wide material-operation">
    <div className="panel-title"><div><p className="eyebrow">Folder</p><h2>Folder connection</h2></div></div>
    <p className="operation-intro">Check a relative folder path before connecting it to this material. Server locations are never shown.</p>
    <FolderDiscovery key={`discovery-${material.id}-${material.technicalIdentity}-${material.updatedAt}`} materialId={material.id}
      identity={material.technicalIdentity} disabled={checking || linking || refreshing} onSelect={(path) => {
        setFolderPath(path); setPreflight(undefined); setCheckedPath(""); setPathError(""); setError(""); setNotice("Folder selected. Check it before linking."); folderInput.current?.focus();
      }} />
    {error && <ActionError message={error} />}
    <p className="operation-notice" role="status" aria-live="polite">{notice}</p>
    <form onSubmit={checkFolder} aria-label="Check material folder" noValidate>
      <label className="form-field" htmlFor="material-folder-path">Relative folder path</label>
      <div className="inline-form">
        <input
          ref={folderInput}
          id="material-folder-path"
          value={folderPath}
          disabled={checking || linking || refreshing}
          required
          aria-invalid={Boolean(pathError)}
          aria-describedby={pathError ? "material-folder-path-error" : undefined}
          placeholder={`library/${material.technicalIdentity}`}
          onChange={(event) => {
            setFolderPath(event.target.value);
            setPreflight(undefined);
            setCheckedPath("");
            setPathError("");
            setError("");
            setNotice("");
          }}
        />
        <button className="button" type="submit" disabled={checking || linking || refreshing}>
          {checking ? "Checking…" : "Check folder"}
        </button>
      </div>
      {pathError && <p id="material-folder-path-error" className="field-error">{pathError}</p>}
    </form>
    {preflight && <>
      <PreflightResult result={preflight} expectedIdentity={material.technicalIdentity} />
      <button
        ref={linkButton}
        className="button button--primary"
        type="button"
        disabled={!canLink || checking || linking || refreshing}
        aria-haspopup="dialog"
        onClick={() => {
          restoreDialogFocus.current = true;
          dialog.current?.showModal();
        }}
      >
        {linking ? "Linking…" : "Link folder"}
      </button>
      {!canLink && <p className="muted">The folder can be linked only when its identity matches and the check says it is safe to continue.</p>}
    </>}
    <ConfirmDialog
      dialogRef={dialog}
      returnFocusRef={linkButton}
      restoreFocusOnCloseRef={restoreDialogFocus}
      title="Link this folder?"
      confirmLabel="Link folder"
      pendingLabel="Linking…"
      pending={linking}
      onConfirm={() => void linkFolder()}
    >
      <p>This will connect the relative folder <strong>{checkedPath}</strong> to <strong>{material.technicalIdentity}</strong>.</p>
      <p>The server will check the folder again before saving the connection.</p>
    </ConfirmDialog>
  </article>;
}


function MaterialDetailContent({ initialMaterial, client, navigate, reload, includeArchived }: {
  initialMaterial: Material; client: ApiClient; navigate: (path: string) => void; reload: () => void; includeArchived: boolean;
}) {
  const role = useSession()?.session.user.role;
  const [material, setMaterial] = useState(initialMaterial);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState("");
  const [propertiesBusy, setPropertiesBusy] = useState(false);
  const store = useMemo(() => new GalleryStore(), []);
  useEffect(() => () => store.clear(), [store]);
  const loadOptions = useCallback(() => Promise.all([client.getProjects(), client.getBrands(), client.getInternalUsers()]), [client]);
  const options = useResource(loadOptions);
  const [projects = [], brands = [], users = []] = options.data ?? [];
  const refresh = useCallback(async (fallback?: Material) => {
    if (fallback) setMaterial(fallback);
    setRefreshing(true); setRefreshError("");
    try {
      const updated = await client.getMaterial(material.id, includeArchived || role === "ADMIN");
      setMaterial(updated);
      if (updated.isArchived !== material.isArchived) navigate(updated.isArchived ? `/material-archives/${material.id}` : `/materials/${material.id}`);
      return true;
    }
    catch { setRefreshError("Change saved, but the material could not be reloaded. Refresh the material data."); return false; }
    finally { setRefreshing(false); }
  }, [client, material.id, material.isArchived, includeArchived, role, navigate]);
  const refreshFromProperties = useCallback(() => { void refresh(); }, [refresh]);
  const canEdit = role !== "LEADERSHIP" && !material.isArchived;
  return <section>
    <NavigationLink className="back-link" href={material.isArchived ? "/material-archives" : "/materials"} navigate={navigate}>{material.isArchived ? "Back to archived materials" : "Back to materials"}</NavigationLink>
    <div className="page-heading"><div><p className="eyebrow">{material.technicalIdentity}</p><h1>{material.materialName}</h1></div>
      {canEdit && <NavigationLink className="button" href={`/materials/${material.id}/edit`} navigate={navigate}>Edit material</NavigationLink>}
    </div>
    {role && <MaterialGallery key={`gallery-${material.id}-${material.folderPath}-${material.updatedAt}`} materialId={material.id} linked={Boolean(material.folderPath)} initiallyOpen />}
    <article className="panel" aria-label="Material properties">
      {options.error && <p role="alert">Related property choices could not be loaded. <button onClick={options.retry}>Retry related records</button></p>}
      <MaterialsTable detail materials={[material]} store={store} client={client} projects={projects} brands={brands} users={users}
        navigate={navigate} refresh={refreshFromProperties} onBusyChange={setPropertiesBusy} onMaterialChanged={setMaterial} />
    </article>
    {refreshError && <div role="alert">{refreshError}<button className="button" onClick={() => void refresh()}>Reload material data</button></div>}
    {role && <MaterialFolderContents key={`contents-${material.id}-${material.folderPath}`} materialId={material.id} folderPath={material.folderPath} />}
    {canEdit && <FolderControls key={`folder-${material.id}`} material={material} client={client} refreshing={refreshing || propertiesBusy} onSaved={refresh} />}
    {role && !material.isArchived && <MaterialMetadataEditor key={`metadata-${material.id}`} material={material} onChanged={refresh} />}
    {role && !material.isArchived && <MaterialContentPanel key={`content-${material.id}-${material.publishedBrandId}`} material={material} onChanged={reload} />}
    <PackagingJobsPanel key={`packaging-${material.id}`} materialId={material.id} onChanged={refresh} />
    <ResourceHistoryPanel kind="MATERIAL" id={material.id} updatedAt={material.updatedAt} />
  </section>;
}

export function MaterialDetailPage({ id, client, navigate, includeArchived = false }: { id: string; client: ApiClient; navigate: (path: string) => void; includeArchived?: boolean }) {
  const load = useCallback(() => client.getMaterial(id, includeArchived), [id, client, includeArchived]);
  const { data, error, cause, retry } = useResource(load);
  if (error) return <ErrorState message={materialLoadError(cause)} retry={retry} />;
  if (!data) return <LoadingState label="Loading material…" />;
  return <MaterialDetailContent key={`${data.id}:${data.updatedAt}:${data.isArchived}`} initialMaterial={data} client={client} navigate={navigate} reload={retry} includeArchived={includeArchived} />;
}
