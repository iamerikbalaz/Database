import { NavigationLink } from "../components/NavigationLink";
import { useResource } from "../api/useResource";
import { useCallback } from "react";
import type { ApiClient } from "../api/client";
import type { ProjectDetail } from "../types";
import { ErrorState, LoadingState } from "../components/PageState";
import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/StatusBadge";

export function ProjectDetailPage({
  id,
  client,
  navigate,
}: {
  id: string;
  client: ApiClient;
  navigate: (path: string) => void;
}) {
  const request = useCallback(() => client.getProject(id), [client, id]);
  const {
    data: item,
    error,
    retry: load,
  } = useResource<ProjectDetail>(request);
  if (error)
    return (
      <>
        <NavigationLink
          className="back-link"
          href={"/projects"}
          navigate={navigate}
        >
          <Icon name="back" size={18} />
          Back to projects
        </NavigationLink>
        <ErrorState
          message="This project could not be found or loaded."
          retry={load}
        />
      </>
    );
  if (!item) return <LoadingState label="Loading project…" />;
  return (
    <section>
      <NavigationLink
        className="back-link"
        href={"/projects"}
        navigate={navigate}
      >
        <Icon name="back" size={18} />
        Back to projects
      </NavigationLink>
      <div className="page-heading detail-heading">
        <div>
          <p className="project-number">{item.number}</p>
          <div className="title-row">
            <h1>{item.name}</h1>
            <StatusBadge status={item.status} />
          </div>
          <p>
            {item.description ?? "Project details and production overview."}
          </p>
        </div>
        <NavigationLink
          className="button button--secondary"
          href={"/projects/" + id + "/edit"}
          navigate={navigate}
        >
          Edit project
        </NavigationLink>
      </div>
      <article className="panel panel--wide">
        <div className="panel-title">
          <div>
            <p className="eyebrow">Overview</p>
            <h2>Project information</h2>
          </div>
        </div>
        <dl className="info-list info-list--columns">
          <div>
            <dt>Client</dt>
            <dd>{item.clientName}</dd>
          </div>
          <div>
            <dt>Deadline</dt>
            <dd>{item.dueDate ?? "Not set"}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{item.createdAt}</dd>
          </div>
          <div>
            <dt>Last updated</dt>
            <dd>{item.updatedAt}</dd>
          </div>
        </dl>
      </article>
    </section>
  );
}
