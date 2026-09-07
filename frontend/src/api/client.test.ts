import { afterEach, describe, expect, it, vi } from "vitest";
import { companies, brands, projects } from "./mockData";
import {
 companyFromDto, companyToDto, publishedBrandFromDto, publishedBrandToDto,
 projectFromDto, projectToDto, parseCompany, parsePublishedBrand, parseProject,
 statusFromDto, statusToDto,
} from "./dto";
import { apiClient, httpApiClient, mockApiClient } from "./client";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks(); });

const companyDto = companyToDto(companies[0]);
const brandDto = publishedBrandToDto(brands[0]);
const projectDto = projectToDto(projects[0]);
function serve(overrides: Record<string, unknown> = {}, failed?: string) {
 const responses: Record<string, unknown> = {
  "/api/companies": [companyDto],
  "/api/companies/c-1": companyDto,
  "/api/brands": brands.map(publishedBrandToDto),
  "/api/projects": projects.map(projectToDto),
  "/api/projects/p-1": projectDto,
  ...overrides,
 };
 const fetchMock = vi.fn(async (input: string) => {
  if (!(input in responses) || input === failed) return new Response(null, { status: 404 });
  return new Response(JSON.stringify(responses[input]), { status: 200 });
 });
 vi.stubGlobal("fetch", fetchMock);
 return fetchMock;
}
describe("API contract", () => {
 it("maps every Company field in both directions, preserving nulls", () => {
  expect(companyDto).toEqual({
   id: "c-1", name: "Swisspearl", legal_name: "Swisspearl", country: "CZ",
   address: null, website: null, vat_id: null, notion_page_id: null, is_active: true,
   created_at: companies[0].createdAt, updated_at: companies[0].updatedAt,
  });
  expect(companyFromDto(parseCompany(companyDto))).toEqual(companies[0]);
  expect(companyToDto(companyFromDto({ ...companyDto, is_active: false, website: "https://example.com/" })))
   .toEqual({ ...companyDto, is_active: false, website: "https://example.com/" });
 });
 it("maps full PublishedBrand including managed counter and timestamps", () => {
  expect(brandDto).toMatchObject({ company_id: "c-1", folder_prefix: "SWISSPEARL", brand_identifier: "swisspearl", next_sequence_number: 1, is_active: true });
  expect(publishedBrandFromDto(parsePublishedBrand(brandDto))).toEqual(brands[0]);
  expect(publishedBrandToDto(publishedBrandFromDto(brandDto))).toEqual(brandDto);
 });
 it("maps project number, owner, notes, dates and timestamps", () => {
  expect(projectDto).toMatchObject({ company_id: "c-1", project_number: "RWT-2026-018", due_date: "2026-09-18", notes: projects[0].description, status: "IN_PROGRESS" });
  expect(projectFromDto(parseProject(projectDto))).toEqual(projects[0]);
  expect(projectToDto(projectFromDto({ ...projectDto, notes: null, due_date: null })))
   .toEqual({ ...projectDto, notes: null, due_date: null });
 });
 it("maps exactly the three supported statuses", () => {
  expect(statusFromDto("NOT_STARTED")).toBe("not_started");
  expect(statusFromDto("IN_PROGRESS")).toBe("in_progress");
  expect(statusFromDto("DONE")).toBe("done");
  expect(statusToDto("not_started")).toBe("NOT_STARTED");
  expect(statusToDto("in_progress")).toBe("IN_PROGRESS");
  expect(statusToDto("done")).toBe("DONE");
  expect(() => parseProject({ ...projectDto, status: "review" })).toThrow();
 });
 it("rejects malformed payloads instead of trusting a type assertion", () => {
  expect(() => parseCompany({ ...companyDto, is_active: "true" })).toThrow();
  expect(() => parsePublishedBrand({ ...brandDto, next_sequence_number: 10000 })).toThrow();
 });
 it("uses HTTP by default and preserves the /api prefix", async () => {
  const fetchMock = serve();
  expect(apiClient).toBe(httpApiClient);
  expect(await apiClient.getCompanies()).toEqual([companies[0]]);
  expect(fetchMock).toHaveBeenCalledWith("/api/companies", expect.any(Object));
 });
 it("allows explicitly injected mock data without HTTP", async () => {
  const fetchMock = serve();
  expect(await mockApiClient.getCompany("c-1")).toMatchObject({ name: "Swisspearl", brands: [brands[0]], projects: [projects[0]] });
  expect(fetchMock).not.toHaveBeenCalled();
 });
 it("assembles CompanyDetail from three endpoints and filters both relations", async () => {
  const fetchMock = serve();
  expect(await httpApiClient.getCompany("c-1")).toEqual({ ...companies[0], brands: [brands[0]], projects: [projects[0]] });
  expect(fetchMock).toHaveBeenCalledTimes(3);
 });
 it.each(["/api/companies/c-1", "/api/brands", "/api/projects"])("rejects detail when %s fails", async path => {
  serve({}, path);
  await expect(httpApiClient.getCompany("c-1")).rejects.toThrow("404");
 });
 it("rejects malformed related data instead of exposing undefined arrays", async () => {
  serve({ "/api/brands": {} });
  await expect(httpApiClient.getCompany("c-1")).rejects.toThrow("Invalid API list");
 });
 it("loads project detail and resolves its company name", async () => {
  serve();
  expect(await httpApiClient.getProject("p-1")).toEqual({ ...projects[0], clientName: "Swisspearl" });
 });
 it("loads the project list from HTTP", async () => {
  serve();
  expect(await httpApiClient.getProjects()).toEqual(projects);
 });
 it("reports missing IDs", async () => {
  serve();
  await expect(httpApiClient.getCompany("missing")).rejects.toThrow("404");
  await expect(mockApiClient.getProject("missing")).rejects.toThrow("not found");
 });
});

describe("explicit mock configuration", () => {
 it("allows opt-in mocks in development", async () => {
  vi.resetModules();
  vi.stubEnv("DEV", true);
  vi.stubEnv("VITE_USE_MOCK_API", "true");
  const clients = await import("./client");
  expect(clients.apiClient).toBe(clients.mockApiClient);
 });
 it("keeps the production client on HTTP even with the mock flag set", async () => {
  vi.resetModules();
  vi.stubEnv("DEV", false);
  vi.stubEnv("VITE_USE_MOCK_API", "true");
  const clients = await import("./client");
  expect(clients.apiClient).toBe(clients.httpApiClient);
 });
});
