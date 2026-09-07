import { responseError } from "./errors";
import type {
  CompanyCreateDto,
  CompanyPatchDto,
  BrandCreateDto,
  BrandPatchDto,
  ProjectCreateDto,
  ProjectPatchDto,
} from "./writeDto";
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
  getCompanyRecord(id: string): Promise<Company>;
  getBrand(id: string): Promise<PublishedBrand>;
  getProjectRecord(id: string): Promise<Project>;
  createCompany(payload: CompanyCreateDto): Promise<Company>;
  updateCompany(id: string, payload: CompanyPatchDto): Promise<Company>;
  createBrand(payload: BrandCreateDto): Promise<PublishedBrand>;
  updateBrand(id: string, payload: BrandPatchDto): Promise<PublishedBrand>;
  createProject(payload: ProjectCreateDto): Promise<Project>;
  updateProject(id: string, payload: ProjectPatchDto): Promise<Project>;
  getCompany(id: string): Promise<CompanyDetail>;
  getBrands(): Promise<PublishedBrand[]>;
  getProjects(): Promise<Project[]>;
  getProject(id: string): Promise<ProjectDetail>;
}
const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "/api").replace(
  /\/$/,
  "",
);
async function request(
  path: string,
  method = "GET",
  payload?: object,
): Promise<unknown> {
  const response = await fetch(apiBaseUrl + path, {
    method,
    body: payload === undefined ? undefined : JSON.stringify(payload),
    headers: {
      Accept: "application/json",
      ...(payload === undefined ? {} : { "Content-Type": "application/json" }),
    },
  });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw responseError(response.status, body);
  }
  return response.json();
}
export const httpApiClient: ApiClient = {
  async getCompanyRecord(id) {
    return companyFromDto(
      parseCompany(await request("/companies/" + encodeURIComponent(id))),
    );
  },
  async getBrand(id) {
    return publishedBrandFromDto(
      parsePublishedBrand(await request("/brands/" + encodeURIComponent(id))),
    );
  },
  async getProjectRecord(id) {
    return projectFromDto(
      parseProject(await request("/projects/" + encodeURIComponent(id))),
    );
  },
  async createCompany(payload) {
    return companyFromDto(
      parseCompany(await request("/companies", "POST", payload)),
    );
  },
  async updateCompany(id, payload) {
    return companyFromDto(
      parseCompany(
        await request("/companies/" + encodeURIComponent(id), "PATCH", payload),
      ),
    );
  },
  async createBrand(payload) {
    return publishedBrandFromDto(
      parsePublishedBrand(await request("/brands", "POST", payload)),
    );
  },
  async updateBrand(id, payload) {
    return publishedBrandFromDto(
      parsePublishedBrand(
        await request("/brands/" + encodeURIComponent(id), "PATCH", payload),
      ),
    );
  },
  async createProject(payload) {
    return projectFromDto(
      parseProject(await request("/projects", "POST", payload)),
    );
  },
  async updateProject(id, payload) {
    return projectFromDto(
      parseProject(
        await request("/projects/" + encodeURIComponent(id), "PATCH", payload),
      ),
    );
  },
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
const mockWriteDisabled = async (): Promise<never> => {
  throw new Error("Saving is unavailable in mock mode. Use the HTTP API.");
};
export const mockApiClient: ApiClient = {
  createCompany: mockWriteDisabled,
  updateCompany: mockWriteDisabled,
  createBrand: mockWriteDisabled,
  updateBrand: mockWriteDisabled,
  createProject: mockWriteDisabled,
  updateProject: mockWriteDisabled,
  async getCompanyRecord(id) {
    const item = companies.find((c) => c.id === id);
    if (!item) throw new Error("Company not found.");
    return item;
  },
  async getBrand(id) {
    const item = brands.find((b) => b.id === id);
    if (!item) throw new Error("Brand not found.");
    return item;
  },
  async getProjectRecord(id) {
    const item = projects.find((p) => p.id === id);
    if (!item) throw new Error("Project not found.");
    return item;
  },
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
