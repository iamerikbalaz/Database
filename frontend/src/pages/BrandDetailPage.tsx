import { useCallback } from "react";
import type { ApiClient } from "../api/client";
import { useResource } from "../api/useResource";
import { NavigationLink } from "../components/NavigationLink";
import { ErrorState, LoadingState } from "../components/PageState";
import { StatusBadge } from "../components/StatusBadge";

export function BrandDetailPage({
  id,
  client,
  navigate,
}: {
  id: string;
  client: ApiClient;
  navigate: (path: string) => void;
}) {
  const load = useCallback(async () => {
    const brand = await client.getBrand(id);
    const company = await client.getCompanyRecord(brand.companyId);
    return { brand, company };
  }, [client, id]);
  const { data, error, retry } = useResource(load);
  if (error)
    return (
      <ErrorState
        message="This published brand could not be found or loaded."
        retry={retry}
      />
    );
  if (!data) return <LoadingState label="Loading published brand…" />;
  const { brand, company } = data;
  return (
    <section>
      <NavigationLink
        className="back-link"
        href={"/companies/" + company.id}
        navigate={navigate}
      >
        Back to company
      </NavigationLink>
      <div className="page-heading">
        <div>
          <h1>{brand.name}</h1>
          <StatusBadge status={brand.isActive ? "active" : "inactive"} />
        </div>
        <NavigationLink
          className="button"
          href={"/brands/" + brand.id + "/edit"}
          navigate={navigate}
        >
          Edit published brand
        </NavigationLink>
      </div>
      <article className="panel">
        <dl className="info-list">
          <div>
            <dt>Company</dt>
            <dd>
              <NavigationLink
                href={"/companies/" + company.id}
                navigate={navigate}
              >
                {company.name}
              </NavigationLink>
            </dd>
          </div>
          <div>
            <dt>Folder prefix</dt>
            <dd>{brand.folderPrefix}</dd>
          </div>
          <div>
            <dt>Brand identifier</dt>
            <dd>{brand.brandIdentifier}</dd>
          </div>
          <div>
            <dt>Next sequence number</dt>
            <dd>{brand.nextSequenceNumber} (system managed)</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{brand.createdAt}</dd>
          </div>
          <div>
            <dt>Updated</dt>
            <dd>{brand.updatedAt}</dd>
          </div>
        </dl>
      </article>
    </section>
  );
}
