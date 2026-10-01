import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import { record } from "./api/dto";
import { parseMaterial, type MaterialDto } from "./api/materialDto";
import { inactiveDto, materialBrand, materialDto, materialProject, metadataDto, processorDto } from "./test/materialFixtures";
import { materialCategories } from "./data/materialCategories";

const createdBatch = { id: "60000000-0000-4000-8000-000000000001", status: "COMPLETED", completed_count: 1, total_count: 1,
  items: [{ material_id: materialDto.id, name: "NEW-SURFACE", technical_identity: "LASVIT_0001_NEW-SURFACE_G02", folder_path: "LASVIT/LASVIT_0001_NEW-SURFACE_G02", status: "COMPLETED", error_code: null }] };

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
type Write = { path: string; method: string; body: Record<string, unknown> };
function backend(options: {
  items?: MaterialDto[];
  failed?: string;
  failureStatus?: number;
  offline?: boolean;
  projectless?: boolean;
  emptyChoices?: boolean;
  write?: (write: Write) => Promise<Response>;
} = {}) {
  let current: MaterialDto = { ...materialDto };
  if (options.projectless) current.project_id = null;
  const writes: Write[] = [];
  const queries: URLSearchParams[] = [];
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const url = new URL(path, "http://localhost");
    if (options.offline) throw new TypeError("Network unavailable");
    if (url.pathname === options.failed) return response({ detail: "Unavailable" }, options.failureStatus ?? 404);
    const method = init?.method ?? "GET";
    if (method !== "GET") {
      const body = record(JSON.parse(String(init?.body)));
      const write = { path, method, body };
      writes.push(write);
      if (options.write) return options.write(write);
      if (url.pathname === "/api/material-create-batches") return response(createdBatch);
      current = parseMaterial({ ...current, ...body });
      return response(current, method === "POST" ? 201 : 200);
    }
    if (url.pathname === "/api/materials") {
      queries.push(url.searchParams);
      const list = options.items ?? [current];
      return response(list.filter((m) => [...url.searchParams].every(([key, value]) => {
        if (key === "search") return (m.material_name + " " + m.technical_identity).toLowerCase().includes(value.toLowerCase());
        return String(record(m)[key]) === value;
      })));
    }
    const records: Record<string, unknown> = {
      "/api/online-categories": materialCategories.map((item, index) => ({ id: `70000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`, value: item.value, abbreviation: item.code, version: 1, is_active: true })),
      "/api/collections": [],
      "/api/material-create-options": { paths_version: 0, templates: [{ name: "base.sbs", size_bytes: 123 }] },
      "/api/customers": options.emptyChoices ? [] : [{ id: materialBrand.id, name: materialBrand.name, status: "Active cooperation", is_active: true,
        folder_prefix: materialBrand.folder_prefix, brand_identifier: materialBrand.brand_identifier, main_category_codes: [], has_logo: false, notion_page_id: null,
        website: null, address: null, shipping_address: null, legal_name: null, vat_id: null, description: null, notes: null, created_at: materialDto.created_at, updated_at: materialDto.updated_at }],
      "/api/orders": options.emptyChoices ? [] : [{ id: materialProject.id, number: "0001", customer_id: materialBrand.id, project_type: "SCANNING", starting_date: null,
        due_date: null, notes: null, responsible_id: null, status: "Not started", priority: null, generated_name: materialProject.name, folder_path: null, notion_page_id: null,
        created_at: materialDto.created_at, updated_at: materialDto.updated_at }],
      ["/api/materials/" + current.id]: current,
      "/api/projects": options.emptyChoices ? [] : [materialProject],
      "/api/brands": options.emptyChoices ? [] : [materialBrand],
      // Include an inactive record even on active-only requests to verify UI filtering.
      "/api/internal-users": options.emptyChoices ? [] : [processorDto, inactiveDto],
      ["/api/projects/" + materialProject.id]: materialProject,
      ["/api/brands/" + materialBrand.id]: materialBrand,
      ["/api/internal-users/" + processorDto.id]: processorDto,
      ["/api/materials/" + current.id + "/metadata"]: { ...metadataDto, material_id: current.id },
      ["/api/materials/" + current.id + "/metadata/snapshots"]: [],
    };
    return url.pathname in records ? response(records[url.pathname]) : response({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { writes, queries, fetchMock };
}
afterEach(() => { sessionStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const change = (label: string, value: string) => fireEvent.change((screen.queryByRole("search", { name: "Material filters" }) ? within(screen.getByRole("search", { name: "Material filters" })) : screen).getByLabelText(label, { exact: true }), { target: { value } });
async function fillCreate() {
  await screen.findByRole("form", { name: "Add material" });
  change("Order", materialProject.id);
  change("Material name", "  New surface  ");
  change("Main category", "G02");
  change("Processor", processorDto.id);
  change("SBS template", "base.sbs");
}
const submit = () => fireEvent.click(screen.queryByRole("button", { name: "Create material" }) ?? screen.getByRole("button", { name: "Save" }));

it("loads the material list, resolves names and opens detail via a native link", async () => {
  backend();
  render(<App initialPath="/materials" />);
  expect(screen.getByText("Loading materials…")).toBeInTheDocument();
  const link = await screen.findByRole("link", { name: materialDto.technical_identity });
  expect(link).toHaveAttribute("href", "/materials/" + materialDto.id);
  await waitFor(() => expect(within(screen.getByRole("table")).getByText(processorDto.display_name)).toBeInTheDocument());
  const table = within(screen.getByRole("table"));
  for (const text of [materialDto.material_name, materialProject.name, materialBrand.name, "G03 · Facade / Tiles", "In progress", "no"])
    expect(table.getByText(text)).toBeInTheDocument();
  link.focus();
  expect(link).toHaveFocus();
  fireEvent.click(link);
  expect(await screen.findByRole("heading", { name: materialDto.material_name })).toBeInTheDocument();
});
it("handles an empty material list", async () => {
  backend({ items: [] }); render(<App initialPath="/materials" />);
  expect(await screen.findByText("No materials found")).toBeInTheDocument();
});
it("opens a historical material without requiring a project", async () => {
  backend({ projectless: true });
  render(<App initialPath="/materials" />);
  expect(await screen.findByText("No order assigned")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: materialDto.technical_identity }));
  await screen.findByRole("heading", { name: materialDto.material_name });
  expect(screen.getByText("No order assigned")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Edit material" })).not.toBeInTheDocument();
});
it.each([false, true])("handles API/network list failure (offline=%s) and retries", async (offline) => {
  backend({ failed: "/api/materials", failureStatus: 503, offline });
  render(<App initialPath="/materials" />);
  await screen.findByText("We couldn't load the data");
  backend();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("link", { name: materialDto.technical_identity })).toBeInTheDocument();
});
it.each([
  ["Search materials", "search", "Crystal"], ["Order", "project_id", materialProject.id],
  ["Customer", "published_brand_id", materialBrand.id], ["Processor", "assigned_processor_id", processorDto.id],
  ["Main category", "main_category_code", "G03"], ["Status", "workflow_status", "IN_PROGRESS"],
  ["Checked", "checked_status", "no"],
  ["Published", "is_published", "false"],
])("sends %s as the exact API query parameter", async (label, key, value) => {
  const { queries } = backend(); render(<App initialPath="/materials" />);
  await screen.findByRole("option", { name: processorDto.display_name });
  change(label, value);
  await waitFor(() => expect(queries.at(-1)?.get(key)).toBe(value));
  expect(await screen.findByRole("link", { name: materialDto.technical_identity })).toBeInTheDocument();
});
it("combines all filters, honors an empty response and clears filters", async () => {
  const { queries } = backend(); render(<App initialPath="/materials" />);
  await screen.findByRole("option", { name: processorDto.display_name });
  for (const [label, value] of [["Search materials", "missing"], ["Order", materialProject.id], ["Customer", materialBrand.id], ["Processor", processorDto.id], ["Main category", "G03"], ["Status", "DONE"], ["Checked", "OK"], ["Published", "true"]]) change(label, value);
  await waitFor(() => expect(queries.at(-1)?.size).toBe(8));
  expect(await screen.findByText("No materials found")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  await waitFor(() => expect(queries.at(-1)?.size).toBe(0));
  expect(await screen.findByRole("link", { name: materialDto.technical_identity })).toBeInTheDocument();
});
it("loads full detail including UUID, four-digit number, relations, path and states", async () => {
  backend(); render(<App initialPath={"/materials/" + materialDto.id} />);
  await screen.findByRole("heading", { name: materialDto.material_name });
  for (const text of [materialDto.id, "9999", "G03 · Facade / Tiles", "In progress", "no"]) expect(screen.getByText(text)).toBeInTheDocument();
  expect(screen.getByText("Folder path").nextElementSibling).toHaveTextContent("No folder linked");
  expect(await screen.findByText(materialProject.name)).toBeInTheDocument();
  expect(screen.getByText(materialBrand.name)).toBeInTheDocument();
  expect(screen.getByText(processorDto.display_name)).toBeInTheDocument();
  expect(screen.getByText(new Date(materialDto.created_at).toLocaleString())).toBeInTheDocument();
  expect(screen.getByText(new Date(materialDto.updated_at).toLocaleString())).toBeInTheDocument();
  for (const name of ["Done", "Approve", "Publish", "Open folder"]) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
});
it.each([
  ["/api/projects", "Related property choices could not be loaded"],
  ["/api/internal-users", "Related property choices could not be loaded"],
  ["/api/brands", "Related property choices could not be loaded"],
])("preserves detail when related record %s is missing", async (failed, message) => {
  backend({ failed }); render(<App initialPath={"/materials/" + materialDto.id} />);
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(screen.getByRole("heading", { name: materialDto.material_name })).toBeInTheDocument();
});
it("shows material 404", async () => {
  backend({ failed: "/api/materials/" + materialDto.id }); render(<App initialPath={"/materials/" + materialDto.id} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("not found (404)");
});
it.each(["/materials/not-a-uuid", "/materials/not-a-uuid/edit"])("rejects invalid route %s without HTTP", (path) => {
  const { fetchMock } = backend(); render(<App initialPath={path} />);
  expect(screen.getByRole("alert")).toHaveTextContent("valid UUID");
  expect(fetchMock).not.toHaveBeenCalled();
});
it("creates with the Order Customer, active processors and one recoverable batch POST", async () => {
  const { writes, fetchMock } = backend(); render(<App initialPath="/materials/new" />);
  expect(await screen.findByLabelText("Order")).toHaveFocus();
  expect(screen.queryByRole("option", { name: inactiveDto.display_name })).not.toBeInTheDocument();
  await fillCreate(); submit();
  expect(await screen.findByRole("heading", { name: "1 of 1 folders created" })).toBeInTheDocument();
  expect(screen.getByLabelText("Customer")).toHaveValue(materialBrand.id);
  expect(screen.getByLabelText("Customer")).toBeDisabled();
  expect(fetchMock).toHaveBeenCalledWith("/api/internal-users?is_active=true", expect.any(Object));
  expect(writes).toEqual([{ path: "/api/material-create-batches", method: "POST", body: {
    idempotency_key: expect.any(String), expected_paths_version: 0,
    project_id: materialProject.id, published_brand_id: materialBrand.id, names: ["New surface"],
    main_category_code: "G02", assigned_processor_id: processorDto.id, resolution: 8, template_name: "base.sbs", category_ids: [], collection_ids: [],
  } }]);
});
it("edits with a minimal PATCH and shows managed fields read-only", async () => {
  const { writes } = backend(); render(<App initialPath={"/materials/" + materialDto.id + "/edit"} />);
  expect(await screen.findByLabelText("Material name *")).toHaveValue(materialDto.material_name);
  expect(screen.queryByLabelText("Customer *")).not.toBeInTheDocument();
  expect(screen.getByText("Current record (read-only)")).toBeInTheDocument();
  change("Material name *", "Updated surface"); submit();
  await screen.findByText("Material updated successfully.");
  expect(writes).toEqual([{ path: "/api/materials/" + materialDto.id, method: "PATCH", body: { material_name: "Updated surface" } }]);
});
it("explains category conflict as a future rename and preserves input", async () => {
  backend({ write: async () => response({ detail: "main_category_code cannot be changed while folder_path is set." }, 409) });
  render(<App initialPath={"/materials/" + materialDto.id + "/edit"} />);
  await screen.findByLabelText("Main category *"); change("Main category *", "G02"); submit();
  expect(await screen.findByRole("alert")).toHaveTextContent("future rename operation");
  expect(screen.getByRole("alert")).toHaveFocus();
  expect(screen.getByLabelText("Main category *")).toHaveValue("G02");
  expect(screen.getByLabelText("Main category *")).toHaveAttribute("aria-describedby", "field-mainCategoryCode-error");
});
it("explains rejected create values, focuses the summary and preserves input", async () => {
  let finish: (result: Response) => void = () => {};
  const { writes } = backend({ write: () => new Promise((resolve) => { finish = resolve; }) });
  render(<App initialPath="/materials/new" />); await fillCreate(); submit();
  await waitFor(() => expect(writes).toHaveLength(1));
  await act(async () => finish(response({ detail: [{ loc: ["body", "material_name"], msg: "Invalid material name" }] }, 422)));
  const summary = await screen.findByRole("alert");
  expect(summary).toHaveFocus();
  expect(summary).toHaveTextContent("Invalid material name");
  expect(screen.getByLabelText("Material name")).toHaveValue("  New surface  ");
  await waitFor(() => expect(screen.getByRole("button", { name: "Create material" })).toBeEnabled());
  await act(async () => { await Promise.resolve(); });
  expect(summary).toHaveFocus();
  expect(screen.getByLabelText("Order")).not.toHaveFocus();
});
it("blocks double submit while a write is pending", async () => {
  let finish: (r: Response) => void = () => {};
  const { writes } = backend({ write: () => new Promise((resolve) => { finish = resolve; }) });
  render(<App initialPath="/materials/new" />); await fillCreate();
  const form = screen.getByRole("form", { name: "Add material" });
  fireEvent.submit(form); fireEvent.submit(form);
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(screen.getByLabelText("Material name")).toBeDisabled();
  await act(async () => finish(response(createdBatch)));
  await screen.findByText("1 of 1 folders created");
});
it("handles empty option lists without sending invalid create", async () => {
  const { writes } = backend({ emptyChoices: true }); render(<App initialPath="/materials/new" />);
  await screen.findByRole("form", { name: "Add material" }); submit();
  expect(screen.getByRole("alert")).toHaveTextContent("Enter 1–100 names");
  expect(writes).toHaveLength(0);
});
it("rejects malformed category locally", async () => {
  const { writes } = backend(); render(<App initialPath="/materials/new" />);
  await fillCreate(); change("Main category", "../G03"); submit();
  expect(screen.getByRole("alert")).toHaveFocus();
  expect(writes).toHaveLength(0);
});
