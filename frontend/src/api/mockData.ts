import type { Brand, Company, Project } from "../types";
export const companies: Company[] = [
  { id: "c-1", name: "Swisspearl", officialName: "Swisspearl Česká republika a.s.", status: "active", websiteUrl: "https://www.swisspearl.com", email: "materials@swisspearl.com", phone: "+420 311 744 111", address: "Lidická 302, Beroun", description: "Facade and interior material manufacturer." },
  { id: "c-2", name: "Lasvit", officialName: "LASVIT s.r.o.", status: "active", websiteUrl: "https://www.lasvit.com", email: "studio@lasvit.com", address: "Komunardů 894/32, Praha 7" },
  { id: "c-3", name: "Preciosa Lighting", status: "active", websiteUrl: "https://www.preciosalighting.com", email: "info@preciosalighting.com", address: "Kamenický Šenov" },
  { id: "c-4", name: "RAVAK", officialName: "RAVAK a.s.", status: "inactive", websiteUrl: "https://www.ravak.cz", address: "Obecnická 285, Příbram" },
  { id: "c-5", name: "Brokis", officialName: "BROKIS s.r.o.", status: "active", websiteUrl: "https://www.brokis.cz", email: "info@brokis.cz", address: "Španielova 1315/25, Praha" },
];
export const brands: Brand[] = [
  { id: "b-1", companyId: "c-1", name: "Swisspearl", identifier: "SWISSPEARL", active: true }, { id: "b-2", companyId: "c-1", name: "Cembrit", identifier: "CEMBRIT", active: false }, { id: "b-3", companyId: "c-2", name: "Lasvit", identifier: "LASVIT", active: true }, { id: "b-4", companyId: "c-3", name: "Preciosa", identifier: "PRECIOSA", active: true }, { id: "b-5", companyId: "c-5", name: "Brokis", identifier: "BROKIS", active: true },
];
export const projects: Project[] = [
  { id: "p-1", number: "RWT-2026-018", name: "Swisspearl facade collection", companyId: "c-1", clientName: "Swisspearl", status: "in_progress", dueDate: "2026-09-18", description: "Digitisation of the current facade surface collection." },
  { id: "p-2", number: "RWT-2026-014", name: "Crystal lighting materials", companyId: "c-2", clientName: "Lasvit", status: "review", dueDate: "2026-09-10" },
  { id: "p-3", number: "RWT-2026-011", name: "Signature glass library", companyId: "c-3", clientName: "Preciosa Lighting", status: "done", dueDate: "2026-08-29" },
  { id: "p-4", number: "RWT-2026-021", name: "Bathroom surfaces 2027", companyId: "c-4", clientName: "RAVAK", status: "not_started", dueDate: "2026-10-12" },
  { id: "p-5", number: "RWT-2026-022", name: "Handblown glass collection", companyId: "c-5", clientName: "Brokis", status: "in_progress", dueDate: "2026-10-02" },
];
