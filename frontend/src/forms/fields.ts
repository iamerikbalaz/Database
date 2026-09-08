import type { Company, Project, PublishedBrand } from "../types";
import type {
  CompanyWriteDto,
  BrandWriteDto,
  ProjectWriteDto,
} from "../api/writeDto";

export type Values = Record<string, string>;
export interface Field {
  name: string;
  apiName: string;
  label: string;
  type?: "text" | "url" | "date" | "textarea" | "select" | "checkbox";
  required?: boolean;
  maxLength?: number;
  disabled?: boolean;
  pattern?: RegExp;
  options?: { value: string; label: string; disabled?: boolean }[];
}
export const companyFields: Field[] = [
  {
    name: "name",
    apiName: "name",
    label: "Name",
    required: true,
    maxLength: 255,
  },
  {
    name: "officialName",
    apiName: "legal_name",
    label: "Legal name",
    maxLength: 255,
  },
  { name: "country", apiName: "country", label: "Country", maxLength: 100 },
  { name: "address", apiName: "address", label: "Address", type: "textarea" },
  {
    name: "websiteUrl",
    apiName: "website",
    label: "Website",
    type: "url",
    maxLength: 2048,
  },
  { name: "vatId", apiName: "vat_id", label: "VAT ID", maxLength: 100 },
  {
    name: "notionPageId",
    apiName: "notion_page_id",
    label: "Notion page ID",
    maxLength: 255,
  },
  { name: "isActive", apiName: "is_active", label: "Active", type: "checkbox" },
];
export const companyOptions = (companies: Company[]) =>
  companies.map((c) => ({
    value: c.id,
    label: c.name + (c.status === "inactive" ? " (inactive)" : ""),
  }));
export const ownerField = (companies: Company[], disabled = false): Field => ({
  name: "companyId",
  apiName: "company_id",
  label: "Company",
  type: "select",
  required: true,
  options: companyOptions(companies),
  disabled,
});
export const brandFields: Field[] = [
  {
    name: "name",
    apiName: "name",
    label: "Name",
    required: true,
    maxLength: 255,
  },
  {
    name: "folderPrefix",
    apiName: "folder_prefix",
    label: "Folder prefix",
    required: true,
    maxLength: 255,
  },
  {
    name: "brandIdentifier",
    apiName: "brand_identifier",
    label: "Brand identifier",
    required: true,
    maxLength: 255,
  },
  { name: "isActive", apiName: "is_active", label: "Active", type: "checkbox" },
];
export const projectFields: Field[] = [
  {
    name: "name",
    apiName: "name",
    label: "Name",
    required: true,
    maxLength: 255,
  },
  {
    name: "number",
    apiName: "project_number",
    label: "Project number",
    required: true,
    maxLength: 100,
  },
  {
    name: "status",
    apiName: "status",
    label: "Status",
    type: "select",
    required: true,
    options: [
      { value: "not_started", label: "Not started" },
      { value: "in_progress", label: "In progress" },
      { value: "done", label: "Done" },
    ],
  },
  { name: "dueDate", apiName: "due_date", label: "Due date", type: "date" },
  { name: "description", apiName: "notes", label: "Notes", type: "textarea" },
];
const optional = (value: string) => value.trim() || null;
export function companyRequest(v: Values): CompanyWriteDto {
  return {
    name: v.name.trim(),
    legal_name: optional(v.officialName),
    country: optional(v.country),
    address: optional(v.address),
    website: optional(v.websiteUrl),
    vat_id: optional(v.vatId),
    notion_page_id: optional(v.notionPageId),
    is_active: v.isActive === "true",
  };
}
export function brandRequest(v: Values): BrandWriteDto {
  return {
    company_id: v.companyId,
    name: v.name.trim(),
    folder_prefix: v.folderPrefix.trim(),
    brand_identifier: v.brandIdentifier.trim(),
    is_active: v.isActive === "true",
  };
}
export function projectRequest(v: Values): ProjectWriteDto {
  const status =
    v.status === "not_started"
      ? "NOT_STARTED"
      : v.status === "in_progress"
        ? "IN_PROGRESS"
        : v.status === "done"
          ? "DONE"
          : null;
  if (!status) throw new Error("Choose a valid status.");
  return {
    company_id: v.companyId,
    name: v.name.trim(),
    project_number: v.number.trim(),
    status,
    due_date: optional(v.dueDate),
    notes: optional(v.description),
  };
}
export const companyValues = (c?: Company): Values => ({
  name: c?.name ?? "",
  officialName: c?.officialName ?? "",
  country: c?.country ?? "",
  address: c?.address ?? "",
  websiteUrl: c?.websiteUrl ?? "",
  vatId: c?.vatId ?? "",
  notionPageId: c?.notionPageId ?? "",
  isActive: String(c ? c.status === "active" : true),
});
export const brandValues = (b?: PublishedBrand, companyId = ""): Values => ({
  name: b?.name ?? "",
  companyId: b?.companyId ?? companyId,
  folderPrefix: b?.folderPrefix ?? "",
  brandIdentifier: b?.brandIdentifier ?? "",
  isActive: String(b?.isActive ?? true),
});
export const projectValues = (p?: Project): Values => ({
  name: p?.name ?? "",
  companyId: p?.companyId ?? "",
  number: p?.number ?? "",
  status: p?.status ?? "not_started",
  dueDate: p?.dueDate ?? "",
  description: p?.description ?? "",
});
export function validate(
  fields: Field[],
  values: Values,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const value = (values[field.name] ?? "").trim();
    if (field.required && !value)
      errors[field.name] = field.label + " is required.";
    else if (field.maxLength && [...value].length > field.maxLength)
      errors[field.name] =
        field.label + " must be at most " + field.maxLength + " characters.";
    else if (field.pattern && value && !field.pattern.test(value))
      errors[field.name] = field.label + " must contain only letters, digits and hyphens.";
    else if (
      field.type === "select" &&
      value &&
      !field.options?.some((o) => o.value === value)
    )
      errors[field.name] = "Choose a valid " + field.label.toLowerCase() + ".";
    else if (field.type === "url" && value) {
      try {
        const url = new URL(value);
        if (!["http:", "https:"].includes(url.protocol)) throw new Error();
      } catch {
        errors[field.name] = "Enter a valid http:// or https:// URL.";
      }
    } else if (field.type === "date" && value) {
      const date = new Date(value + "T00:00:00Z");
      if (
        !/^\d{4}-\d{2}-\d{2}$/.test(value) ||
        !Number.isFinite(date.getTime()) ||
        date.toISOString().slice(0, 10) !== value ||
        value.startsWith("0000")
      )
        errors[field.name] = "Enter a valid date.";
    }
  }
  return errors;
}
