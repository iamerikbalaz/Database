import { useCallback } from "react";
import type { ApiClient } from "../api/client";
import { materialLoadError } from "../api/materialClient";
import type { MaterialPatchDto } from "../api/materialDto";
import { changedFields } from "../api/writeDto";
import { useResource } from "../api/useResource";
import { ErrorState, LoadingState } from "../components/PageState";
import { RecordForm, type FormDefinition } from "../forms/RecordForm";
import type { Field, Values } from "../forms/fields";
import { MaterialFacts } from "./MaterialDetailPage";

function editable(v: Values): Required<MaterialPatchDto> {
  return { project_id: v.projectId, material_name: v.materialName.trim(),
    main_category_code: v.mainCategoryCode.trim().toUpperCase(), assigned_processor_id: v.assignedProcessorId };
}
export function MaterialEditorPage({ id, client, navigate, onSaved }: {
  id?: string; client: ApiClient; navigate: (path: string) => void; onSaved: (path: string, message: string) => void;
}) {
  const load = useCallback(async () => {
    const [material, projects, brands, users] = await Promise.all([
      id ? client.getMaterial(id) : Promise.resolve(undefined), client.getProjects(), client.getBrands(), client.getInternalUsers(true),
    ]);
    const active = users.filter((u) => u.isActive);
    const initial: Values = {
      projectId: material?.projectId ?? "", publishedBrandId: material?.publishedBrandId ?? "",
      materialName: material?.materialName ?? "", mainCategoryCode: material?.mainCategoryCode ?? "",
      assignedProcessorId: material?.assignedProcessorId ?? "",
    };
    const processorOptions: NonNullable<Field["options"]> = active.map((u) => ({ value: u.id, label: u.displayName }));
    const unavailableProcessor = material && !active.some((u) => u.id === material.assignedProcessorId);
    if (unavailableProcessor) processorOptions.push({ value: material.assignedProcessorId, label: "Current processor (inactive or unavailable): " + material.assignedProcessorId, disabled: true });
    const fields: Field[] = [
      { name: "projectId", apiName: "project_id", label: "Project", type: "select", required: true,
        options: projects.map((p) => ({ value: p.id, label: p.name })) },
      ...(!id ? [{ name: "publishedBrandId", apiName: "published_brand_id", label: "Published brand", type: "select" as const, required: true,
        options: brands.map((b) => ({ value: b.id, label: b.name })) }] : []),
      { name: "materialName", apiName: "material_name", label: "Material name", required: true, maxLength: 255 },
      { name: "mainCategoryCode", apiName: "main_category_code", label: "Main category", required: true, maxLength: 100, pattern: /^[A-Za-z0-9-]+$/ },
      { name: "assignedProcessorId", apiName: "assigned_processor_id", label: "Processor", type: "select", required: true, options: processorOptions },
    ];
    const definition: FormDefinition = {
      title: id ? "Edit material" : "Add material", fields, initial, cancel: id ? "/materials/" + id : "/materials",
      save: async (v) => {
        const input = editable(v);
        const saved = id
          ? await client.updateMaterial(id, changedFields(input, editable(initial)))
          : await client.createMaterial({ ...input, published_brand_id: v.publishedBrandId });
        return { path: "/materials/" + saved.id, message: id ? "Material updated successfully." : "Material created successfully." };
      },
    };
    return { definition, material, brands, unavailableProcessor,
      missingChoices: !projects.length || (!id && !brands.length) || (!material && !active.length) };
  }, [id, client]);
  const { data, error, cause, retry } = useResource(load);
  if (error) return <ErrorState message={materialLoadError(cause)} retry={retry} />;
  if (!data) return <LoadingState label="Loading material form…" />;
  return <>
    {data.missingChoices && <p role="alert" className="form-error">A project, published brand and active processor must be available before creating a material.</p>}
    {data.unavailableProcessor && <p role="status">The current processor is inactive or unavailable. Only active processors can be selected as a replacement.</p>}
    <RecordForm key={id ?? "new"} definition={data.definition} navigate={navigate} onSaved={onSaved} />
    {data.material && <article className="panel panel--wide" aria-label="Current material record">
      <h2>Current record (read-only)</h2>
      <p>Published brand: {data.brands.find((b) => b.id === data.material?.publishedBrandId)?.name ?? data.material.publishedBrandId}</p>
      <MaterialFacts material={data.material} />
    </article>}
  </>;
}
