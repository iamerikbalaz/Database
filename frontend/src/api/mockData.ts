import type { Company, PublishedBrand, Project } from "../types";
const timestamps = { createdAt: "2026-06-12T00:00:00Z", updatedAt: "2026-09-03T00:00:00Z" };
export const companies: Company[] = ["Swisspearl", "Lasvit", "Preciosa Lighting", "RAVAK", "Brokis"].map((name, i) => ({
 id: "c-" + (i + 1), name, officialName: name, status: i === 3 ? "inactive" : "active",
 websiteUrl: null, country: "CZ", address: null, vatId: null, notionPageId: null, ...timestamps,
}));
export const brands: PublishedBrand[] = companies.map(c => ({
 id: "b-" + c.id, companyId: c.id, name: c.name, folderPrefix: c.name.toUpperCase(),
 brandIdentifier: c.name.toLowerCase().replaceAll(" ", "-"), nextSequenceNumber: 1,
 isActive: c.status === "active", ...timestamps,
}));
export const projects: Project[] = [
 { id: "p-1", number: "RWT-2026-018", name: "Swisspearl facade collection", companyId: "c-1", status: "in_progress", dueDate: "2026-09-18", description: "Digitisation of the current facade surface collection.", ...timestamps },
 { id: "p-2", number: "RWT-2026-014", name: "Crystal lighting materials", companyId: "c-2", status: "not_started", dueDate: null, description: null, ...timestamps },
 { id: "p-3", number: "RWT-2026-011", name: "Signature glass library", companyId: "c-3", status: "done", dueDate: null, description: null, ...timestamps },
];
