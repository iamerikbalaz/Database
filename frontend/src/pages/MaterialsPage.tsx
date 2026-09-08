import { useCallback, useState } from "react";
import type { ApiClient } from "../api/client";
import { materialLoadError, type MaterialFilters } from "../api/materialClient";
import { publicationStatuses, statusLabel, validationStatuses, workflowStatuses } from "../api/materialDto";
import { useResource } from "../api/useResource";
import { NavigationLink } from "../components/NavigationLink";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";

export function MaterialsPage({ client, navigate }: { client: ApiClient; navigate: (path: string) => void }) {
  const [filters, setFilters] = useState<MaterialFilters>({});
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
    { key: "workflow_status", label: "Workflow status", options: workflowStatuses.map((value) => ({ value, label: statusLabel(value) })) },
    { key: "validation_status", label: "Validation status", options: validationStatuses.map((value) => ({ value, label: statusLabel(value) })) },
    { key: "publication_status", label: "Publication status", options: publicationStatuses.map((value) => ({ value, label: statusLabel(value) })) },
    { key: "is_published", label: "Published", options: [{ value: "true", label: "Yes" }, { value: "false", label: "No" }] },
  ];
  return <section>
    <div className="page-heading"><div><p className="eyebrow">Production</p><h1>Materials</h1><p>Manage material records and their production status.</p></div>
      <NavigationLink className="button button--primary" href="/materials/new" navigate={navigate}>Add material</NavigationLink>
    </div>
    <div className="panel material-filters" role="search" aria-label="Material filters">
      <label className="form-field">Search materials<input type="search" value={filters.search ?? ""} onChange={(e) => setFilters({ ...filters, search: e.target.value })} /></label>
      <label className="form-field">Main category<input value={filters.main_category_code ?? ""} onChange={(e) => setFilters({ ...filters, main_category_code: e.target.value.toUpperCase() })} /></label>
      {selectors.map((s) => <label className="form-field" key={s.key}>{s.label}
        <select value={filters[s.key] ?? ""} onChange={(e) => setFilters({ ...filters, [s.key]: e.target.value })}>
          <option value="">All</option>{s.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>)}
      <button className="button" onClick={() => setFilters({})}>Clear filters</button>
    </div>
    {options.error && <div role="alert" className="form-error">Related names and filter options could not be loaded. IDs are shown instead. <button onClick={options.retry}>Retry related records</button></div>}
    {result.error ? <ErrorState message={materialLoadError(result.cause)} retry={result.retry} />
      : !result.data ? <LoadingState label="Loading materials…" />
      : !result.data.length ? <EmptyState title="No materials found" description="Clear the filters or add a material to get started." />
      : <div className="table-card material-table" role="region" aria-label="Material results" tabIndex={0}>
        <table><caption className="sr-only">Materials and production status</caption><thead><tr>
          {["Technical identity", "Material name", "Project", "Published brand", "Category", "Processor", "Workflow", "Validation", "Publication", "Published"].map((label) => <th scope="col" key={label}>{label}</th>)}
        </tr></thead><tbody>{result.data.map((m) => <tr key={m.id}>
          <td><NavigationLink className="table-link" href={"/materials/" + m.id} navigate={navigate}>{m.technicalIdentity}</NavigationLink></td>
          <td>{m.materialName}</td><td>{projects.find((p) => p.id === m.projectId)?.name ?? m.projectId}</td>
          <td>{brands.find((b) => b.id === m.publishedBrandId)?.name ?? m.publishedBrandId}</td>
          <td>{m.mainCategoryCode}</td><td>{users.find((u) => u.id === m.assignedProcessorId)?.displayName ?? m.assignedProcessorId}</td>
          <td>{statusLabel(m.workflowStatus)}</td><td>{statusLabel(m.validationStatus)}</td><td>{statusLabel(m.publicationStatus)}</td><td>{m.isPublished ? "Yes" : "No"}</td>
        </tr>)}</tbody></table>
      </div>}
  </section>;
}
