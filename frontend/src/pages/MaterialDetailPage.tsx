import { useCallback } from "react";
import type { ApiClient } from "../api/client";
import { materialLoadError } from "../api/materialClient";
import { statusLabel, type Material } from "../api/materialDto";
import { useResource } from "../api/useResource";
import { ErrorState, LoadingState } from "../components/PageState";
import { NavigationLink } from "../components/NavigationLink";

export function MaterialFacts({ material: m }: { material: Material }) {
  const fields = [
    ["Internal UUID", m.id], ["Technical identity", m.technicalIdentity],
    ["Sequence number", String(m.sequenceNumber).padStart(4, "0")],
    ["Material name", m.materialName], ["Main category", m.mainCategoryCode],
    ["Folder path", m.folderPath ?? "Not linked"],
    ["Workflow status", statusLabel(m.workflowStatus)],
    ["Validation status", statusLabel(m.validationStatus)],
    ["Publication status", statusLabel(m.publicationStatus)],
    ["Published", m.isPublished ? "Yes" : "No"], ["Created", m.createdAt], ["Updated", m.updatedAt],
  ];
  return <dl className="info-list material-facts">{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
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
  const { material: m, project, brand, processor } = data;
  return <section>
    <NavigationLink className="back-link" href="/materials" navigate={navigate}>Back to materials</NavigationLink>
    <div className="page-heading"><div><p className="eyebrow">{m.technicalIdentity}</p><h1>{m.materialName}</h1></div>
      <NavigationLink className="button" href={"/materials/" + m.id + "/edit"} navigate={navigate}>Edit material</NavigationLink>
    </div>
    <article className="panel"><MaterialFacts material={m} />
      <dl className="info-list">
        <div><dt>Project</dt><dd>{project.status === "fulfilled" ? <NavigationLink href={"/projects/" + m.projectId} navigate={navigate}>{project.value.name}</NavigationLink> : <span role="alert">Project could not be loaded ({m.projectId}).</span>}</dd></div>
        <div><dt>Published brand</dt><dd>{brand.status === "fulfilled" ? <NavigationLink href={"/brands/" + m.publishedBrandId} navigate={navigate}>{brand.value.name}</NavigationLink> : <span role="alert">Published brand could not be loaded ({m.publishedBrandId}).</span>}</dd></div>
        <div><dt>Processor</dt><dd>{processor.status === "fulfilled" ? processor.value.displayName + (processor.value.isActive ? "" : " (inactive)") : <span role="alert">Processor could not be loaded ({m.assignedProcessorId}).</span>}</dd></div>
      </dl>
      {[project, brand, processor].some((r) => r.status === "rejected") && <button className="button" onClick={retry}>Retry related records</button>}
    </article>
  </section>;
}
