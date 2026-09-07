import { brands, companies, projects } from "./mockData";
import type {
  Company,
  CompanyDetail,
  PublishedBrand,
  Project,
  ProjectDetail,
} from "../types";
import {
  companyFromDto,
  publishedBrandFromDto,
  projectFromDto,
  parseCompany,
  parsePublishedBrand,
  parseProject,
  parseList,
} from "./dto";
export interface ApiClient {
  getCompanies(): Promise<Company[]>;
  getCompany(id: string): Promise<CompanyDetail>;
  getBrands(): Promise<PublishedBrand[]>;
  getProjects(): Promise<Project[]>;
  getProject(id: string): Promise<ProjectDetail>;
}
const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "/api").replace(
  /\/$/,
  "",
);
async function request(path: string): Promise<unknown> {
  const response = await fetch(apiBaseUrl + path, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("Request failed (" + response.status + ")");
  return response.json();
}
export const httpApiClient: ApiClient = {
  async getCompanies() {
    return parseList(await request("/companies"), parseCompany).map(
      companyFromDto,
    );
  },
  async getBrands() {
    return parseList(await request("/brands"), parsePublishedBrand).map(
      publishedBrandFromDto,
    );
  },
  async getProjects() {
    return parseList(await request("/projects"), parseProject).map(
      projectFromDto,
    );
  },
  async getCompany(id) {
    // Lists have no company filter. Reject the entire detail if any request fails.
    const [company, brands, projects] = await Promise.all([
      request("/companies/" + encodeURIComponent(id))
        .then(parseCompany)
        .then(companyFromDto),
      httpApiClient.getBrands(),
      httpApiClient.getProjects(),
    ]);
    return {
      ...company,
      brands: brands.filter((b) => b.companyId === company.id),
      projects: projects.filter((p) => p.companyId === company.id),
    };
  },
  async getProject(id) {
    const project = projectFromDto(
      parseProject(await request("/projects/" + encodeURIComponent(id))),
    );
    const company = companyFromDto(
      parseCompany(
        await request("/companies/" + encodeURIComponent(project.companyId)),
      ),
    );
    return { ...project, clientName: company.name };
  },
};
export const mockApiClient: ApiClient = {
  async getCompanies() {
    return [...companies];
  },
  async getBrands() {
    return [...brands];
  },
  async getProjects() {
    return [...projects];
  },
  async getCompany(id) {
    const company = companies.find((c) => c.id === id);
    if (!company) throw new Error("Company was not found.");
    return {
      ...company,
      brands: brands.filter((b) => b.companyId === id),
      projects: projects.filter((p) => p.companyId === id),
    };
  },
  async getProject(id) {
    const project = projects.find((p) => p.id === id);
    if (!project) throw new Error("Project was not found.");
    return {
      ...project,
      clientName:
        companies.find((c) => c.id === project.companyId)?.name ??
        project.companyId,
    };
  },
};
// Mock mode is opt-in and restricted to local development.
export const apiClient =
  import.meta.env.DEV && import.meta.env.VITE_USE_MOCK_API === "true"
    ? mockApiClient
    : httpApiClient;
