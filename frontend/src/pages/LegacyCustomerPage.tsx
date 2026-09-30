import { useCallback, useEffect } from "react";
import { directoryClient } from "../api/directoryClient";
import { useResource } from "../api/useResource";
import { NavigationLink } from "../components/NavigationLink";
import { ErrorState, LoadingState } from "../components/PageState";
export function LegacyCustomerPage({ id, navigate }: { id: string; navigate: (path: string) => void }) {
  const resource = useResource(useCallback(async () => (await directoryClient.customers()).filter(item => item.legacyCompanyId === id), [id]));
  const only = resource.data?.length === 1 ? resource.data[0].id : undefined;
  useEffect(() => { if (only) navigate(`/customers/${only}`); }, [only, navigate]);
  if (resource.error) return <ErrorState message="The linked customers could not be loaded." retry={resource.retry} />;
  if (!resource.data || only) return <LoadingState label="Opening customer…" />;
  return <section><h1>Customers</h1><p>This company is now represented by the following customers.</p>{resource.data.map(item => <NavigationLink className="directory-related-order" key={item.id} href={`/customers/${item.id}`} navigate={navigate}>{item.name}</NavigationLink>)}<NavigationLink href="/customers" navigate={navigate}>All customers</NavigationLink></section>;
}
