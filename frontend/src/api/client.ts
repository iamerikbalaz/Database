import { brands, companies, projects } from "./mockData";
import type { Brand, Company, CompanyDetail, Project, ProjectDetail } from "../types";
export interface ApiClient { getCompanies(): Promise<Company[]>; getCompany(id: string): Promise<CompanyDetail>; getBrands(): Promise<Brand[]>; getProjects(): Promise<Project[]>; getProject(id: string): Promise<ProjectDetail> }
const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "/api").replace(/\/$/, "");
async function request<T>(path: string): Promise<T> { const response = await fetch(`${apiBaseUrl}${path}`, { headers: { Accept: "application/json" } }); if (!response.ok) throw new Error(`Request failed (${response.status})`); return (await response.json()) as T }
export const httpApiClient: ApiClient = { getCompanies: () => request("/companies"), getCompany: (id) => request(`/companies/${encodeURIComponent(id)}`), getBrands: () => request("/brands"), getProjects: () => request("/projects"), getProject: (id) => request(`/projects/${encodeURIComponent(id)}`) };
const wait = <T,>(value: T) => new Promise<T>((resolve) => window.setTimeout(() => resolve(value), 120));
const notFound = (entity: string) => Promise.reject(new Error(`${entity} was not found.`));
export const mockApiClient: ApiClient = {
  getCompanies: () => wait([...companies]), getBrands: () => wait([...brands]), getProjects: () => wait([...projects]),
  getCompany: (id) => { const company = companies.find((item) => item.id === id); return company ? wait({ ...company, brands: brands.filter((brand) => brand.companyId === id), projects: projects.filter((project) => project.companyId === id) }) : notFound("Company") },
  getProject: (id) => { const project = projects.find((item) => item.id === id); return project ? wait({ ...project, createdAt: "2026-06-12", updatedAt: "2026-09-03" }) : notFound("Project") },
};
export const apiClient = import.meta.env.VITE_USE_REAL_API === "true" ? httpApiClient : mockApiClient;
