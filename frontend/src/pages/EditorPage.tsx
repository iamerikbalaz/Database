import { useCallback } from "react";
import type { ApiClient } from "../api/client";
import { changedFields } from "../api/writeDto";
import { useResource } from "../api/useResource";
import { ErrorState, LoadingState } from "../components/PageState";
import { NavigationLink } from "../components/NavigationLink";
import { RecordForm, type FormDefinition } from "../forms/RecordForm";
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
  const load = useCallback(async (): Promise<FormDefinition> => {
    if (kind === "company") {
      const company = id ? await client.getCompanyRecord(id) : undefined;
      const initial = companyValues(company);
      return {
        title: id ? "Edit company" : "Add company",
        fields: companyFields,
        initial,
        cancel: id ? "/companies/" + id : "/companies",
        save: async (values) => {
          const input = companyRequest(values);
          const saved = id
            ? await client.updateCompany(
                id,
                changedFields(input, companyRequest(initial)),
              )
            : await client.createCompany(input);
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
        save: async (values) => {
          const input = brandRequest(values);
          const saved = id
            ? await client.updateBrand(
                id,
                changedFields(input, brandRequest(initial)),
              )
            : await client.createBrand(input);
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
      save: async (values) => {
        const input = projectRequest(values);
        const saved = id
          ? await client.updateProject(
              id,
              changedFields(input, projectRequest(initial)),
            )
          : await client.createProject(input);
        return {
          path: "/projects/" + saved.id,
          message: id
            ? "Project updated successfully."
            : "Project created successfully.",
        };
      },
    };
  }, [client, kind, id, companyId]);
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
