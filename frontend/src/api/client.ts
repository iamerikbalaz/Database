import { responseError } from "./errors";
import { apiUrl, authenticatedHeaders, notifySessionInvalidation, sessionGeneration } from "../auth/sessionTransport";
import { materialApi, type MaterialApi } from "./materialClient";
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
  uuid,
} from "./dto";
export interface ApiClient extends MaterialApi {
  getCompanies(): Promise<Company[]>;
  getCompanyRecord(id: string): Promise<Company>;
  getBrand(id: string): Promise<PublishedBrand>;
  getProjectRecord(id: string): Promise<Project>;
  createCompany(payload: CompanyCreateDto, requestKey?: string): Promise<Company>;
  updateCompany(id: string, payload: CompanyPatchDto, requestKey?: string): Promise<Company>;
  createBrand(payload: BrandCreateDto, requestKey?: string): Promise<PublishedBrand>;
  updateBrand(id: string, payload: BrandPatchDto, requestKey?: string): Promise<PublishedBrand>;
  createProject(payload: ProjectCreateDto, requestKey?: string): Promise<Project>;
  updateProject(id: string, payload: ProjectPatchDto, requestKey?: string): Promise<Project>;
  getCompany(id: string): Promise<CompanyDetail>;
  getBrands(): Promise<PublishedBrand[]>;
  getProjects(): Promise<Project[]>;
  getProject(id: string): Promise<ProjectDetail>;
}
export async function request(
  path: string,
  method = "GET",
  payload?: object,
  requestKey?: string,
): Promise<unknown> {
  const sentGeneration = sessionGeneration();
  const response = await fetch(apiUrl(path), {
    method,
    credentials: "same-origin",
    cache: "no-store",
    body: payload === undefined ? undefined : JSON.stringify(payload),
    headers: {
      Accept: "application/json",
      ...authenticatedHeaders(method),
      ...(requestKey === undefined ? {} : { "Idempotency-Key": uuid(requestKey) }),
      ...(payload === undefined ? {} : { "Content-Type": "application/json" }),
    },
  });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    notifySessionInvalidation(response.status, body, sentGeneration);
    throw responseError(response.status, body);
  }
  return response.json();
}
export const httpApiClient: ApiClient = {
  ...materialApi(request),
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
  async createCompany(payload, requestKey) {
    return companyFromDto(
      parseCompany(await request("/companies", "POST", payload, requestKey)),
    );
  },
  async updateCompany(id, payload, requestKey) {
    return companyFromDto(
      parseCompany(
        await request("/companies/" + encodeURIComponent(id), "PATCH", payload, requestKey),
      ),
    );
  },
  async createBrand(payload, requestKey) {
    return publishedBrandFromDto(
      parsePublishedBrand(await request("/brands", "POST", payload, requestKey)),
    );
  },
  async updateBrand(id, payload, requestKey) {
    return publishedBrandFromDto(
      parsePublishedBrand(
        await request("/brands/" + encodeURIComponent(id), "PATCH", payload, requestKey),
      ),
    );
  },
  async createProject(payload, requestKey) {
    return projectFromDto(
      parseProject(await request("/projects", "POST", payload, requestKey)),
    );
  },
  async updateProject(id, payload, requestKey) {
    return projectFromDto(
      parseProject(
        await request("/projects/" + encodeURIComponent(id), "PATCH", payload, requestKey),
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
  ...materialApi(async () => { throw new Error("Materials require the HTTP API; mock data is unavailable."); }),
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
