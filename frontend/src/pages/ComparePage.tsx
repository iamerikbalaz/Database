import { useCallback, useState } from "react";
import type { ApiClient } from "../api/client";
import { useResource } from "../api/useResource";
import { MaterialGallery } from "../components/MaterialGallery";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";

export function ComparePage({ client, navigate }: { client: ApiClient; navigate: (path: string) => void }) {
  const load = useCallback(() => client.getMaterials(), [client]);
  const resource = useResource(load);
  const [left, setLeft] = useState(""), [right, setRight] = useState("");
  return <section className="material-comparison"><div className="page-heading"><div><p className="eyebrow">Visual review</p><h1>Compare materials</h1></div></div>
    <p>Choose two materials available to your account. Each preview keeps its own proportions.</p>
    {resource.error ? <ErrorState message="Materials could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState /> : resource.data.length === 0 ? <EmptyState title="No materials available" description="Materials assigned to your account will appear here." /> : <div className="comparison-columns">
      {([{ side: "Left", id: left, other: right, set: setLeft }, { side: "Right", id: right, other: left, set: setRight }]).map(({ side, id, other, set }) => {
        const material = resource.data!.find((item) => item.id === id);
        return <section key={side} className="comparison-column" aria-label={`${side} material`}>
          <label htmlFor={`compare-${side}`}>{side} material</label><select id={`compare-${side}`} value={material?.id ?? ""} onChange={(event) => set(event.target.value)}>
            <option value="">Choose a material</option>{resource.data!.filter((item) => item.id !== other).map((item) => <option key={item.id} value={item.id}>{item.materialName} · {item.technicalIdentity}</option>)}
          </select>
          {material && <><h2>{material.materialName}</h2><dl><dt>Identity</dt><dd>{material.technicalIdentity}</dd><dt>Category</dt><dd>{material.mainCategoryCode}</dd>
            <dt>Workflow</dt><dd>{material.workflowStatus}</dd><dt>Publication</dt><dd>{material.publicationStatus}</dd></dl>
            <button className="button" onClick={() => navigate(`/materials/${material.id}`)}>Open {side.toLowerCase()} material</button>
            <MaterialGallery key={`${material.id}-${material.folderPath}`} materialId={material.id} linked={Boolean(material.folderPath)} initiallyOpen />
          </>}
        </section>;
      })}
    </div>}
    <button className="button" onClick={resource.retry}>Reload comparison materials</button>
  </section>;
}
