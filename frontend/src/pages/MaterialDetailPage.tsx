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
  if (
    path === null || path.startsWith("/") || path.includes("\\") ||
    path.includes(":") || path.split("/").some((part) => part === "" || part === "." || part === "..")
  ) return null;
  return path;
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

function SectionError({ message, retry }: { message: string; retry: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => ref.current?.focus(), [message]);
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
  onLinked,
}: {
  material: Material;
  client: ApiClient;
  onLinked: (fallback: Material) => Promise<boolean>;
}) {
  const [folderPath, setFolderPath] = useState(safeFolderPath(material.folderPath) ?? "");
  const [checkedPath, setCheckedPath] = useState("");
  const [preflight, setPreflight] = useState<MaterialFolderPreflight>();
  const [checking, setChecking] = useState(false);
  const [linking, setLinking] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const pendingCheck = useRef(false);
  const pendingLink = useRef(false);
  const linkButton = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);

  const checkFolder = async (event: FormEvent) => {
    event.preventDefault();
    if (pendingCheck.current) return;
    const path = folderPath.trim();
    pendingCheck.current = true;
    setChecking(true);
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
    checkedPath === folderPath.trim(),
  );

  const linkFolder = async () => {
    if (!canLink || pendingLink.current) return;
    pendingLink.current = true;
    setLinking(true);
    setError("");
    setNotice("");
    try {
      const result = await client.linkMaterialFolder(material.id, checkedPath);
      const refreshed = await onLinked(result.material);
      setNotice(refreshed
        ? `Folder “${checkedPath}” was linked successfully.`
        : `Folder “${checkedPath}” was linked, but the refreshed material detail could not be loaded.`);
      dialog.current?.close();
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
    <form onSubmit={checkFolder} aria-label="Check material folder">
      <label className="form-field" htmlFor="material-folder-path">Relative folder path</label>
      <div className="inline-form">
        <input
          id="material-folder-path"
          value={folderPath}
          disabled={checking || linking}
          required
          placeholder={`library/${material.technicalIdentity}`}
          onChange={(event) => {
            setFolderPath(event.target.value);
            setPreflight(undefined);
            setCheckedPath("");
            setError("");
            setNotice("");
          }}
        />
        <button className="button" type="submit" disabled={checking || linking || !folderPath.trim()}>
          {checking ? "Checking…" : "Check folder"}
        </button>
      </div>
    </form>
    {preflight && <>
      <PreflightResult result={preflight} expectedIdentity={material.technicalIdentity} />
      <button
        ref={linkButton}
        className="button button--primary"
        type="button"
        disabled={!canLink || checking || linking}
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
  onDone,
}: {
  material: Material;
  client: ApiClient;
  onDone: (fallback: Material) => Promise<boolean>;
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
      const refreshed = await onDone(result.material);
      setNotice(refreshed
        ? "Material was marked as Done. Metadata and snapshot history were refreshed."
        : "Material was marked as Done. The latest detail could not be reloaded; metadata refresh is still in progress.");
      dialog.current?.close();
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
        disabled={pending}
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
        pending={pending}
        onConfirm={() => void markDone()}
      >
        <p>The linked folder will be checked again and a metadata snapshot will be saved.</p>
        <p>Missing or incorrect metadata creates a warning, but does not by itself prevent the material from being marked Done. Folder safety or identity problems can still block the action.</p>
      </ConfirmDialog>
    </>}
  </div>;
}

function CurrentMetadataPanel({ id, client }: { id: string; client: ApiClient }) {
  const load = useCallback(() => client.getMaterialMetadata(id), [client, id]);
  const result = useResource(load);
  return <article className="panel metadata-panel">
    <div className="panel-title"><div><p className="eyebrow">Latest scan</p><h2>Current metadata</h2></div></div>
    {result.error ? <SectionError message={materialOperationError(result.cause, "metadata")} retry={result.retry} />
      : !result.data ? <SectionLoading label="Loading current metadata…" />
      : <MetadataValues value={result.data} />}
  </article>;
}

function SnapshotHistoryPanel({ id, client }: { id: string; client: ApiClient }) {
  const load = useCallback(() => client.getMaterialMetadataSnapshots(id), [client, id]);
  const result = useResource(load);
  const snapshots = result.data ? [...result.data].sort((a, b) => a.sequenceNumber - b.sequenceNumber || a.id.localeCompare(b.id)) : undefined;
  return <article className="panel metadata-panel">
    <div className="panel-title"><div><p className="eyebrow">Audit trail</p><h2>Snapshot history</h2></div><span className="count-pill">{snapshots?.length ?? "–"}</span></div>
    {result.error ? <SectionError message={materialOperationError(result.cause, "snapshots")} retry={result.retry} />
      : !snapshots ? <SectionLoading label="Loading snapshot history…" />
      : snapshots.length === 0 ? <div className="section-state"><p>No metadata snapshots yet.</p><span className="muted">A snapshot is added when the material is marked Done.</span></div>
      : <ol className="snapshot-list">{snapshots.map((snapshot) => <li key={snapshot.id}>
          <details>
            <summary><span>Snapshot {snapshot.sequenceNumber}</span><time dateTime={snapshot.loadedAt ?? snapshot.createdAt}>{snapshot.loadedAt ?? snapshot.createdAt}</time></summary>
            <MetadataValues value={snapshot} />
          </details>
        </li>)}</ol>}
  </article>;
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
  const [material, setMaterial] = useState(initialMaterial);
  const [metadataVersion, setMetadataVersion] = useState(0);

  const refreshMaterial = async (fallback: Material) => {
    setMaterial(fallback);
    try {
      setMaterial(await client.getMaterial(fallback.id));
      return true;
    } catch {
      return false;
    }
  };
  const done = async (fallback: Material) => {
    const refreshed = await refreshMaterial(fallback);
    setMetadataVersion((value) => value + 1);
    return refreshed;
  };

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
      <MarkDoneControl material={material} client={client} onDone={done} />
    </article>
    <FolderControls material={material} client={client} onLinked={refreshMaterial} />
    <div className="metadata-grid">
      <CurrentMetadataPanel key={`current-${metadataVersion}`} id={material.id} client={client} />
      <SnapshotHistoryPanel key={`history-${metadataVersion}`} id={material.id} client={client} />
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
