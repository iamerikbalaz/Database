import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import type { ApiClient } from "../api/client";
import {
  materialLoadError,
  materialOperationError,
} from "../api/materialClient";
import { validateFolderPath } from "../api/folderPathValidation";
import { statusLabel, type Material } from "../api/materialDto";
import type {
  MaterialFinding,
  MaterialFolderPreflight,
  MaterialMetadata,
  MaterialMetadataSnapshot,
} from "../api/materialOperationsDto";
import { useResource } from "../api/useResource";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorState, LoadingState } from "../components/PageState";
import { NavigationLink } from "../components/NavigationLink";

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
  useEffect(() => ref.current?.focus(), [message]);
  return (
    <div ref={ref} className="form-error" role="alert" tabIndex={-1}>
      <strong>Action could not be completed</strong>
      <p>{message}</p>
    </div>
  );
}

function SectionError({
  message,
  retry,
  focusOnMount = true,
}: {
  message: string;
  retry: () => void;
  focusOnMount?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focusOnMount) ref.current?.focus();
  }, [focusOnMount, message]);
  return (
    <div ref={ref} className="section-state section-state--error" role="alert" tabIndex={-1}>
      <p>{message}</p>
      <button className="button" type="button" onClick={retry}>Try again</button>
    </div>
  );
}

function SectionLoading({ label }: { label: string }) {
  return <div className="section-state" role="status"><span className="spinner" /><p>{label}</p></div>;
}

type ResourceResult<T> =
  | { state: "loading" }
  | { state: "ready"; data: T }
  | { state: "error"; cause: unknown };

type SavedOperation = "link" | "done";

function RefreshError({
  operation,
  pending,
  retry,
}: {
  operation: SavedOperation;
  pending: boolean;
  retry: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => ref.current?.focus(), [operation]);
  const savedChange = operation === "link"
    ? "The folder was linked"
    : "The material was marked as Done";
  return (
    <div ref={ref} className="form-error" role="alert" tabIndex={-1}>
      <strong>Change saved, refresh incomplete</strong>
      <p>{savedChange}, but some updated data could not be reloaded. Try loading the material data again.</p>
      <button className="button" type="button" disabled={pending} onClick={retry}>
        {pending ? "Reloading…" : "Reload material data"}
      </button>
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

function MetadataValues({ value }: { value: MaterialMetadata | MaterialMetadataSnapshot }) {
  const fields: Array<[string, ReactNode]> = [
    ["Status", <span className={`metadata-status metadata-status--${value.status.toLowerCase()}`}>{statusLabel(value.status)}</span>],
    ["Source filename", <Value>{value.sourceFilename}</Value>],
    ["HEX color", value.hexColor ? <span className="color-value"><span className="color-swatch" style={{ backgroundColor: value.hexColor }} aria-hidden="true" />{value.hexColor}</span> : <Value>{null}</Value>],
    ["Real width", value.widthCm ? `${value.widthCm} cm` : <Value>{null}</Value>],
    ["Real height", value.heightCm ? `${value.heightCm} cm` : <Value>{null}</Value>],
    ["Master resolution", <Value>{value.masterResolution}</Value>],
    ["Loaded at", value.loadedAt ? <time dateTime={value.loadedAt}>{value.loadedAt}</time> : <Value>{null}</Value>],
  ];
  return <>
    <dl className="info-list metadata-values">
      {fields.map(([label, content]) => <div key={label}><dt>{label}</dt><dd>{content}</dd></div>)}
    </dl>
    <FindingList title="Warnings and metadata issues" items={value.warnings} />
    <div className="findings"><h3>Errors</h3><p className="muted">The metadata endpoints do not provide a separate errors list.</p></div>
  </>;
}

export function MaterialFacts({ material: m }: { material: Material }) {
  const visibleFolderPath = safeFolderPath(m.folderPath);
  const fields = [
    ["Internal UUID", m.id], ["Technical identity", m.technicalIdentity],
    ["Sequence number", String(m.sequenceNumber).padStart(4, "0")],
    ["Material name", m.materialName], ["Main category", m.mainCategoryCode],
    ["Folder status", m.folderPath ? "Linked" : "Not linked"],
    ["Folder path", visibleFolderPath ?? (m.folderPath ? "Unavailable (unsafe path hidden)" : "Not linked")],
    ["Workflow status", statusLabel(m.workflowStatus)],
    ["Validation status", statusLabel(m.validationStatus)],
    ["Publication status", statusLabel(m.publicationStatus)],
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
      setNotice(result.canContinue && result.identityMatches
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
    checkedPath === folderPath,
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
      dialog.current?.close();
      setError(materialOperationError(cause, "link"));
    } finally {
      pendingLink.current = false;
      setLinking(false);
    }
  };

  return <article className="panel panel--wide material-operation">
    <div className="panel-title"><div><p className="eyebrow">Folder</p><h2>Folder connection</h2></div></div>
    <p className="operation-intro">Check a relative folder path before connecting it to this material. Server locations are never shown.</p>
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
        onClick={() => dialog.current?.showModal()}
      >
        {linking ? "Linking…" : "Link folder"}
      </button>
      {!canLink && <p className="muted">The folder can be linked only when its identity matches and the check says it is safe to continue.</p>}
    </>}
    <ConfirmDialog
      dialogRef={dialog}
      returnFocusRef={linkButton}
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

function MarkDoneControl({
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
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const pendingRef = useRef(false);
  const button = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const eligible = Boolean(safeFolderPath(material.folderPath) && material.workflowStatus !== "DONE");
  if (!eligible && !pending && !error && !notice) return null;

  const markDone = async () => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setError("");
    setNotice("");
    try {
      const result = await client.markMaterialDone(material.id);
      dialog.current?.close();
      const refreshed = await onSaved(result.material, "done");
      setNotice(refreshed
        ? "Material was marked as Done. Metadata and snapshot history were refreshed."
        : "");
    } catch (cause) {
      dialog.current?.close();
      setError(materialOperationError(cause, "done"));
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  };

  return <div className="done-control">
    {error && <ActionError message={error} />}
    <p className="operation-notice" role="status" aria-live="polite">{notice}</p>
    {eligible && <>
      <button
        ref={button}
        className="button button--primary"
        type="button"
        disabled={pending || refreshing}
        aria-haspopup="dialog"
        onClick={() => dialog.current?.showModal()}
      >
        {pending ? "Marking as Done…" : "Mark as Done"}
      </button>
      <ConfirmDialog
        dialogRef={dialog}
        returnFocusRef={button}
        title="Mark this material as Done?"
        confirmLabel="Mark as Done"
        pendingLabel="Marking as Done…"
        pending={pending || refreshing}
        onConfirm={() => void markDone()}
      >
        <p>The linked folder will be checked again and a metadata snapshot will be saved.</p>
        <p>Missing or incorrect metadata creates a warning, but does not by itself prevent the material from being marked Done. Folder safety or identity problems can still block the action.</p>
      </ConfirmDialog>
    </>}
  </div>;
}

function CurrentMetadataPanel({
  result,
  retry,
  focusError,
}: {
  result: ResourceResult<MaterialMetadata>;
  retry: () => void;
  focusError: boolean;
}) {
  return <article className="panel metadata-panel">
    <div className="panel-title"><div><p className="eyebrow">Latest scan</p><h2>Current metadata</h2></div></div>
    {result.state === "error" ? <SectionError message={materialOperationError(result.cause, "metadata")} retry={retry} focusOnMount={focusError} />
      : result.state === "loading" ? <SectionLoading label="Loading current metadata…" />
      : <MetadataValues value={result.data} />}
  </article>;
}

function SnapshotHistoryPanel({
  result,
  retry,
  focusError,
}: {
  result: ResourceResult<MaterialMetadataSnapshot[]>;
  retry: () => void;
  focusError: boolean;
}) {
  const snapshots = result.state === "ready"
    ? [...result.data].sort((a, b) => a.sequenceNumber - b.sequenceNumber || a.id.localeCompare(b.id))
    : undefined;
  return <article className="panel metadata-panel">
    <div className="panel-title"><div><p className="eyebrow">Audit trail</p><h2>Snapshot history</h2></div><span className="count-pill">{snapshots?.length ?? "–"}</span></div>
    {result.state === "error" ? <SectionError message={materialOperationError(result.cause, "snapshots")} retry={retry} focusOnMount={focusError} />
      : snapshots === undefined ? <SectionLoading label="Loading snapshot history…" />
      : snapshots.length === 0 ? <div className="section-state"><p>No metadata snapshots yet.</p><span className="muted">A snapshot is added when the material is marked Done.</span></div>
      : <ol className="snapshot-list">{snapshots.map((snapshot) => <li key={snapshot.id}>
          <details>
            <summary><span>Snapshot {snapshot.sequenceNumber}</span><time dateTime={snapshot.loadedAt ?? snapshot.createdAt}>{snapshot.loadedAt ?? snapshot.createdAt}</time></summary>
            <MetadataValues value={snapshot} />
          </details>
        </li>)}</ol>}
  </article>;
}

function useMaterialDataRefresh(initialMaterial: Material, client: ApiClient) {
  const materialId = initialMaterial.id;
  const [material, setMaterial] = useState(initialMaterial);
  const [metadata, setMetadata] = useState<ResourceResult<MaterialMetadata>>({ state: "loading" });
  const [snapshots, setSnapshots] = useState<ResourceResult<MaterialMetadataSnapshot[]>>({ state: "loading" });
  const [refreshing, setRefreshing] = useState(false);
  const [refreshFailure, setRefreshFailure] = useState<SavedOperation | null>(null);
  const [refreshNotice, setRefreshNotice] = useState("");
  const requestGeneration = useRef(0);
  const refreshInFlight = useRef<Promise<boolean> | null>(null);

  useEffect(() => {
    const generation = ++requestGeneration.current;
    void Promise.allSettled([
      Promise.resolve().then(() => client.getMaterialMetadata(materialId)),
      Promise.resolve().then(() => client.getMaterialMetadataSnapshots(materialId)),
    ]).then(([metadataResult, snapshotsResult]) => {
      if (requestGeneration.current !== generation) return;
      setMetadata(metadataResult.status === "fulfilled"
        ? { state: "ready", data: metadataResult.value }
        : { state: "error", cause: metadataResult.reason });
      setSnapshots(snapshotsResult.status === "fulfilled"
        ? { state: "ready", data: snapshotsResult.value }
        : { state: "error", cause: snapshotsResult.reason });
    });
    return () => {
      requestGeneration.current += 1;
    };
  }, [client, materialId]);

  const refreshAll = useCallback((fallback: Material | undefined, operation: SavedOperation | undefined) => {
    if (refreshInFlight.current) return refreshInFlight.current;

    const generation = ++requestGeneration.current;
    if (fallback) setMaterial(fallback);
    setMetadata({ state: "loading" });
    setSnapshots({ state: "loading" });
    if (fallback) setRefreshFailure(null);
    setRefreshNotice("");
    setRefreshing(true);

    const work = Promise.allSettled([
      Promise.resolve().then(() => client.getMaterial(materialId)),
      Promise.resolve().then(() => client.getMaterialMetadata(materialId)),
      Promise.resolve().then(() => client.getMaterialMetadataSnapshots(materialId)),
    ]).then(([materialResult, metadataResult, snapshotsResult]) => {
      if (requestGeneration.current !== generation) return false;

      if (materialResult.status === "fulfilled") setMaterial(materialResult.value);
      setMetadata(metadataResult.status === "fulfilled"
        ? { state: "ready", data: metadataResult.value }
        : { state: "error", cause: metadataResult.reason });
      setSnapshots(snapshotsResult.status === "fulfilled"
        ? { state: "ready", data: snapshotsResult.value }
        : { state: "error", cause: snapshotsResult.reason });

      const complete = materialResult.status === "fulfilled" &&
        metadataResult.status === "fulfilled" && snapshotsResult.status === "fulfilled";
      if (complete) setRefreshFailure(null);
      else if (operation) setRefreshFailure(operation);
      return complete;
    });
    const tracked = work.finally(() => {
      if (requestGeneration.current === generation) {
        refreshInFlight.current = null;
        setRefreshing(false);
      }
    });
    refreshInFlight.current = tracked;
    return tracked;
  }, [client, materialId]);

  const retry = useCallback(() => {
    const savedOperation = refreshFailure ?? undefined;
    void refreshAll(undefined, savedOperation).then((complete) => {
      if (complete) setRefreshNotice("Material detail, current metadata and snapshot history were reloaded.");
    });
  }, [refreshAll, refreshFailure]);

  return {
    material,
    metadata,
    snapshots,
    refreshing,
    refreshFailure,
    refreshNotice,
    refreshAll,
    retry,
  };
}

function MaterialDetailContent({
  initialMaterial,
  project,
  brand,
  processor,
  retryRelated,
  client,
  navigate,
}: {
  initialMaterial: Material;
  project: PromiseSettledResult<Awaited<ReturnType<ApiClient["getProjectRecord"]>>>;
  brand: PromiseSettledResult<Awaited<ReturnType<ApiClient["getBrand"]>>>;
  processor: PromiseSettledResult<Awaited<ReturnType<ApiClient["getInternalUser"]>>>;
  retryRelated: () => void;
  client: ApiClient;
  navigate: (path: string) => void;
}) {
  const data = useMaterialDataRefresh(initialMaterial, client);
  const { material } = data;

  return <section>
    <NavigationLink className="back-link" href="/materials" navigate={navigate}>Back to materials</NavigationLink>
    <div className="page-heading"><div><p className="eyebrow">{material.technicalIdentity}</p><h1>{material.materialName}</h1></div>
      <NavigationLink className="button" href={`/materials/${material.id}/edit`} navigate={navigate}>Edit material</NavigationLink>
    </div>
    <article className="panel"><MaterialFacts material={material} />
      <dl className="info-list">
        <div><dt>Project</dt><dd>{project.status === "fulfilled" ? <NavigationLink href={`/projects/${material.projectId}`} navigate={navigate}>{project.value.name}</NavigationLink> : <span role="alert">Project could not be loaded ({material.projectId}).</span>}</dd></div>
        <div><dt>Published brand</dt><dd>{brand.status === "fulfilled" ? <NavigationLink href={`/brands/${material.publishedBrandId}`} navigate={navigate}>{brand.value.name}</NavigationLink> : <span role="alert">Published brand could not be loaded ({material.publishedBrandId}).</span>}</dd></div>
        <div><dt>Processor</dt><dd>{processor.status === "fulfilled" ? processor.value.displayName + (processor.value.isActive ? "" : " (inactive)") : <span role="alert">Processor could not be loaded ({material.assignedProcessorId}).</span>}</dd></div>
      </dl>
      {[project, brand, processor].some((result) => result.status === "rejected") && <button className="button" onClick={retryRelated}>Retry related records</button>}
      <MarkDoneControl
        material={material}
        client={client}
        refreshing={data.refreshing}
        onSaved={data.refreshAll}
      />
    </article>
    <FolderControls
      material={material}
      client={client}
      refreshing={data.refreshing}
      onSaved={data.refreshAll}
    />
    {data.refreshFailure && <RefreshError
      operation={data.refreshFailure}
      pending={data.refreshing}
      retry={data.retry}
    />}
    <p className="operation-notice" role="status" aria-live="polite">{data.refreshNotice}</p>
    <div className="metadata-grid">
      <CurrentMetadataPanel result={data.metadata} retry={data.retry} focusError={!data.refreshFailure} />
      <SnapshotHistoryPanel result={data.snapshots} retry={data.retry} focusError={!data.refreshFailure} />
    </div>
  </section>;
}

export function MaterialDetailPage({ id, client, navigate }: { id: string; client: ApiClient; navigate: (path: string) => void }) {
  const load = useCallback(async () => {
    const material = await client.getMaterial(id);
    const [project, brand, processor] = await Promise.allSettled([
      client.getProjectRecord(material.projectId), client.getBrand(material.publishedBrandId), client.getInternalUser(material.assignedProcessorId),
    ]);
    return { material, project, brand, processor };
  }, [id, client]);
  const { data, error, cause, retry } = useResource(load);
  if (error) return <ErrorState message={materialLoadError(cause)} retry={retry} />;
  if (!data) return <LoadingState label="Loading material…" />;
  return <MaterialDetailContent
    initialMaterial={data.material}
    project={data.project}
    brand={data.brand}
    processor={data.processor}
    retryRelated={retry}
    client={client}
    navigate={navigate}
  />;
}
