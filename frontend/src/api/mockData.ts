import type { Company, PublishedBrand, Project } from "../types";
const timestamps = {
  createdAt: "2026-06-12T00:00:00Z",
  updatedAt: "2026-09-03T00:00:00Z",
};
export const companies: Company[] = [
  "Swisspearl",
  "Lasvit",
  "Preciosa Lighting",
  "RAVAK",
  "Brokis",
].map((name, i) => ({
  id: "10000000-0000-4000-8000-" + String(i + 1).padStart(12, "0"),
  name,
  officialName: name,
  status: i === 3 ? "inactive" : "active",
  websiteUrl: null,
  country: "CZ",
  address: null,
  vatId: null,
  notionPageId: null,
  ...timestamps,
}));
export const brands: PublishedBrand[] = companies.map((c) => ({
  id: c.id.replace(/^1/, "3"),
  companyId: c.id,
  name: c.name,
  folderPrefix: c.name.toUpperCase(),
  brandIdentifier: c.name.toLowerCase().replaceAll(" ", "-"),
  nextSequenceNumber: 1,
  isActive: c.status === "active",
  ...timestamps,
}));
export const projects: Project[] = [
  {
    id: "20000000-0000-4000-8000-000000000001",
    number: "RWT-2026-018",
    name: "Swisspearl facade collection",
    companyId: "10000000-0000-4000-8000-000000000001",
    status: "in_progress",
    dueDate: "2026-09-18",
    description: "Digitisation of the current facade surface collection.",
    ...timestamps,
  },
  {
    id: "20000000-0000-4000-8000-000000000002",
    number: "RWT-2026-014",
    name: "Crystal lighting materials",
    companyId: "10000000-0000-4000-8000-000000000002",
    status: "not_started",
    dueDate: null,
    description: null,
    ...timestamps,
  },
  {
    id: "20000000-0000-4000-8000-000000000003",
    number: "RWT-2026-011",
    name: "Signature glass library",
    companyId: "10000000-0000-4000-8000-000000000003",
    status: "done",
    dueDate: null,
    description: null,
    ...timestamps,
  },
];
