import { NavigationLink } from "../components/NavigationLink";
import { useResource } from "../api/useResource";
import { useCallback, useState } from "react";
import type { ApiClient } from "../api/client";
import type { Company } from "../types";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/StatusBadge";

export function CompaniesPage({
  client,
  navigate,
}: {
  client: ApiClient;
  navigate: (path: string) => void;
}) {
  const [query, setQuery] = useState("");
  const request = useCallback(() => client.getCompanies(), [client]);
  const { data, error, retry: load } = useResource<Company[]>(request);
  const items = data ?? [];
  const state = error ? "error" : data ? "ready" : "loading";
  const filtered = items.filter((c) =>
    `${c.name} ${c.officialName ?? ""}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <section>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Directory</p>
          <h1>Companies</h1>
          <p>Manage clients, their published brands and related projects.</p>
        </div>
        <NavigationLink
          className="button button--primary"
          href={"/companies/new"}
          navigate={navigate}
        >
          <Icon name="plus" size={18} />
          Add company
        </NavigationLink>
      </div>
      <div className="toolbar">
        <label className="search">
          <span className="sr-only">Search companies</span>
          <Icon name="search" size={18} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search companies…"
          />
        </label>
        <span className="result-count">
          {filtered.length} {filtered.length === 1 ? "company" : "companies"}
        </span>
      </div>
      {state === "loading" ? (
        <LoadingState label="Loading companies…" />
      ) : state === "error" ? (
        <ErrorState
          message="Companies are temporarily unavailable. Check your connection and try again."
          retry={load}
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          title={items.length ? "No matching companies" : "No companies yet"}
          description={
            items.length
              ? "Try a different search term."
              : "Add your first company to start building the directory."
          }
        />
      ) : (
        <div className="table-card">
          <table>
            <thead>
              <tr>
                <th>Company</th>
                <th>Website / address</th>
                <th>Status</th>
                <th>
                  <span className="sr-only">Open</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => (
                <tr key={c.id}>
                  <td>
                    <NavigationLink
                      className="table-link"
                      href={`/companies/${c.id}`}
                      navigate={navigate}
                    >
                      {c.name}
                    </NavigationLink>
                    <span>{c.officialName ?? "—"}</span>
                  </td>
                  <td>
                    {c.websiteUrl ?? "No website"}
                    <span>{c.address ?? "—"}</span>
                  </td>
                  <td>
                    <StatusBadge status={c.status} />
                  </td>
                  <td>
                    <Icon name="arrow" size={18} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
