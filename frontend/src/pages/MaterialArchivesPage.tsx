import type { ApiClient } from "../api/client";
import { useSession } from "../auth/context";
import { MaterialsPage } from "./MaterialsPage";
import { MaterialDetailPage } from "./MaterialDetailPage";

export function MaterialArchivesPage({ id, client, navigate }: { id?: string; client: ApiClient; navigate: (path: string) => void }) {
  const auth = useSession();
  if (auth?.session.user.role !== "ADMIN") return <section><h1>Access restricted</h1><p>Only an administrator can manage archived materials.</p></section>;
  return id ? <MaterialDetailPage id={id} client={client} navigate={navigate} includeArchived />
    : <MaterialsPage key="archived-materials" client={client} navigate={navigate} archived />;
}
