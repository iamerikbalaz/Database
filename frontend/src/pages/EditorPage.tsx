import { useCallback } from "react";
import type { ApiClient } from "../api/client";
import { changedFields } from "../api/writeDto";
import { useResource } from "../api/useResource";
import { ErrorState, LoadingState } from "../components/PageState";
import { NavigationLink } from "../components/NavigationLink";
import { RecordForm, type FormDefinition } from "../forms/RecordForm";
import { useSession } from "../auth/context";
import {
  companyFields,
  brandFields,
  projectFields,
  ownerField,
  companyValues,
  brandValues,
  projectValues,
  companyRequest,
  brandRequest,
  projectRequest,
} from "../forms/fields";

export function EditorPage({
  kind,
  id,
  companyId,
  client,
  navigate,
  onSaved,
}: {
  kind: "company" | "brand" | "project";
  id?: string;
  companyId?: string;
  client: ApiClient;
  navigate: (path: string) => void;
  onSaved: (path: string, message: string) => void;
}) {
  const actor = useSession()?.session.user.id;
  const load = useCallback(async (): Promise<FormDefinition> => {
    void actor;
    if (kind === "company") {
      const company = id ? await client.getCompanyRecord(id) : undefined;
      const initial = companyValues(company);
      return {
        title: id ? "Edit company" : "Add company",
        fields: companyFields,
        initial,
        cancel: id ? "/companies/" + id : "/companies",
        command: { kind: "COMPANY", action: id ? "UPDATED" : "CREATED", targetId: id ?? null, editorPath: id ? `/companies/${id}/edit` : "/companies/new",
          payload: (values) => id ? changedFields(companyRequest(values), companyRequest(initial)) : companyRequest(values) },
        save: async (values, key) => {
          const input = companyRequest(values);
          const saved = id
            ? await client.updateCompany(
                id,
                changedFields(input, companyRequest(initial)),
                key,
              )
            : await client.createCompany(input, key);
          return {
            path: "/companies/" + saved.id,
            message: id
              ? "Company updated successfully."
              : "Company created successfully.",
          };
        },
      };
    }
    if (kind === "brand") {
      const brand = id ? await client.getBrand(id) : undefined;
      const companies =
        !id && companyId
          ? [await client.getCompanyRecord(companyId)]
          : await client.getCompanies();
      if (!id && !companyId) throw new Error("Choose a company first.");
      const initial = brandValues(brand, companyId);
      return {
        title: id ? "Edit published brand" : "Add published brand",
        initial,
        fields: [ownerField(companies, !id), ...brandFields],
        cancel: id ? "/brands/" + id : "/companies/" + companyId,
        command: { kind: "BRAND", action: id ? "UPDATED" : "CREATED", targetId: id ?? null, editorPath: id ? `/brands/${id}/edit` : `/companies/${companyId}/brands/new`,
          payload: (values) => id ? changedFields(brandRequest(values), brandRequest(initial)) : brandRequest(values) },
        save: async (values, key) => {
          const input = brandRequest(values);
          const saved = id
            ? await client.updateBrand(
                id,
                changedFields(input, brandRequest(initial)),
                key,
              )
            : await client.createBrand(input, key);
          return {
            path: "/brands/" + saved.id,
            message: id
              ? "Published brand updated successfully."
              : "Published brand created successfully.",
          };
        },
      };
    }
    const [project, companies] = await Promise.all([
      id ? client.getProjectRecord(id) : Promise.resolve(undefined),
      client.getCompanies(),
    ]);
    const initial = projectValues(project);
    return {
      title: id ? "Edit project" : "Add project",
      initial,
      fields: [ownerField(companies), ...projectFields],
      cancel: id ? "/projects/" + id : "/projects",
      command: { kind: "PROJECT", action: id ? "UPDATED" : "CREATED", targetId: id ?? null, editorPath: id ? `/projects/${id}/edit` : "/projects/new",
        payload: (values) => id ? changedFields(projectRequest(values), projectRequest(initial)) : projectRequest(values) },
      save: async (values, key) => {
        const input = projectRequest(values);
        const saved = id
          ? await client.updateProject(
              id,
              changedFields(input, projectRequest(initial)),
              key,
            )
          : await client.createProject(input, key);
        return {
          path: "/projects/" + saved.id,
          message: id
            ? "Project updated successfully."
            : "Project created successfully.",
        };
      },
    };
  }, [client, kind, id, companyId, actor]);
  const { data, error, retry } = useResource(load);
  if (error)
    return (
      <>
        <NavigationLink
          href={kind === "project" ? "/projects" : "/companies"}
          navigate={navigate}
        >
          Back to list
        </NavigationLink>
        <ErrorState
          message="The record or company list could not be loaded. No changes were saved."
          retry={retry}
        />
      </>
    );
  if (!data) return <LoadingState label="Loading form…" />;
  return (
    <RecordForm
      key={data.cancel + data.title}
      definition={data}
      navigate={navigate}
      onSaved={onSaved}
    />
  );
}
