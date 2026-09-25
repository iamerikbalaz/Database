import { useState } from "react";
import type { ImportPreview, ImportResult } from "../api/importClient";
import { NavigationLink } from "./NavigationLink";

export function ImportRows({ data, navigate }: { data: ImportPreview | ImportResult; navigate: (path: string) => void }) {
  const [page, setPage] = useState(0);
  const references = data.references;
  const company = (id: string) => references.companies.find((item) => item.id === id)?.name ?? "Unavailable company";
  return <div className="import-rows">
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Imported material rows"><table>
      <thead><tr><th>Source row</th><th>Material</th><th>Project / company</th><th>Brand / company</th><th>Processor</th></tr></thead>
      <tbody>{data.rows.slice(page * 50, (page + 1) * 50).map((row) => {
        const project = references.projects.find((item) => item.id === row.projectId);
        const brand = references.brands.find((item) => item.id === row.brandId);
        return <tr key={row.sourceRow}><td>{row.sourceRow}</td><td>
          {"materialId" in row && typeof row.materialId === "string"
            ? <NavigationLink href={`/materials/${row.materialId}`} navigate={navigate}>{row.identity}</NavigationLink> : <strong>{row.identity}</strong>}
          <small>{row.name}</small>{row.folderPath && <small>Unverified folder reference: {row.folderPath}</small>}</td>
          <td>{row.projectId === null ? "No project assigned" : project?.name ?? "Unavailable project"}<small>{project ? company(project.companyId) : ""}</small></td>
          <td>{brand?.name ?? "Unavailable brand"}<small>{brand ? company(brand.companyId) : ""}</small></td>
          <td>{references.processors.find((item) => item.id === row.processorId)?.name ?? "Unavailable processor"}</td></tr>;
      })}</tbody>
    </table></div>
    {data.rows.length > 50 && <div className="import-pagination">
      <button className="button" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>Previous rows</button>
      <span>Rows {page * 50 + 1}–{Math.min((page + 1) * 50, data.rows.length)} of {data.rows.length}</span>
      <button className="button" disabled={(page + 1) * 50 >= data.rows.length} onClick={() => setPage((value) => value + 1)}>Next rows</button>
    </div>}
  </div>;
}
