import { useCallback, useEffect, useMemo } from "react";
import type { ApiClient } from "../api/client";
import { statusLabel } from "../api/materialDto";
import { GalleryStore } from "../api/galleryStore";
import { sessionGeneration } from "../auth/sessionTransport";
import { useResource } from "../api/useResource";
import { MaterialThumbnail } from "./MaterialsGrid";
import { NavigationLink } from "./NavigationLink";
import { ErrorState, LoadingState } from "./PageState";
import "./OrderMaterialsPanel.css";

export function OrderMaterialsPanel({ orderId, client, navigate }: { orderId: string; client: ApiClient; navigate: (path: string) => void }) {
  const resource = useResource(useCallback(() => client.getMaterials({ project_id: orderId }), [client, orderId]));
  const generation = sessionGeneration(), store = useMemo(() => new GalleryStore(generation), [generation]);
  useEffect(() => () => store.clear(), [store]);
  const materials = resource.data ?? [], done = materials.filter(material => material.workflowStatus === "DONE").length;
  return <article className="panel order-materials"><h2>Order materials</h2>
    {resource.error ? <ErrorState message="Assigned materials could not be loaded." retry={resource.retry} /> : !resource.data ? <LoadingState label="Loading order materials…" /> : <>
      <div className="order-material-counts"><span>Assigned <strong>{materials.length}</strong></span><span>Done <strong>{done}</strong></span><span>Remaining <strong>{materials.length - done}</strong></span></div>
      <progress aria-label="Order material progress" max={100} value={materials.length ? done / materials.length * 100 : 0} /><p className="muted">Active materials visible to you. Done is the production status, independent of Checked and Automatic check.</p>
      {!materials.length ? <p>No materials assigned. An order can be managed without materials.</p> : <div className="table-card order-material-list"><table><thead><tr><th>Preview</th><th>Material</th><th>Status</th><th>Checked</th><th>Automatic check</th></tr></thead><tbody>
        {materials.map(material => <tr key={material.id}><td><MaterialThumbnail material={material} store={store} /></td><td><NavigationLink className="table-link" href={`/materials/${material.id}`} navigate={navigate}>{material.materialName}</NavigationLink><small>{material.technicalIdentity}</small></td><td>{statusLabel(material.workflowStatus)}</td><td>{material.checkedStatus}</td><td><span className={`automatic-file-check automatic-file-check--${material.automaticFileCheckStatus === "OK" ? "ok" : material.automaticFileCheckStatus === "ISSUES" ? "issues" : "unchecked"}`}>{material.automaticFileCheckStatus === "NOT_CHECKED" ? "Not checked" : material.automaticFileCheckStatus === "ISSUES" ? "Issues" : "OK"}</span></td></tr>)}
      </tbody></table></div>}
    </>}
  </article>;
}
