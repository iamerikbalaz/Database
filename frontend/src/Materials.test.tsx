import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import { record } from "./api/dto";
import { parseMaterial, type MaterialDto } from "./api/materialDto";
import { inactiveDto, materialBrand, materialDto, materialProject, metadataDto, processorDto } from "./test/materialFixtures";

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
type Write = { path: string; method: string; body: Record<string, unknown> };
function backend(options: {
  items?: MaterialDto[];
  failed?: string;
  failureStatus?: number;
  offline?: boolean;
  emptyChoices?: boolean;
  write?: (write: Write) => Promise<Response>;
} = {}) {
  let current = { ...materialDto };
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
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
async function fillCreate() {
  await screen.findByRole("form", { name: "Add material" });
  change("Project *", materialProject.id);
  change("Published brand *", materialBrand.id);
  change("Material name *", "  New surface  ");
  change("Main category *", "g04");
  change("Processor *", processorDto.id);
}
const submit = () => fireEvent.click(screen.getByRole("button", { name: "Save" }));

it("loads the material list, resolves names and opens detail via a native link", async () => {
  backend();
  render(<App initialPath="/materials" />);
  expect(screen.getByText("Loading materials…")).toBeInTheDocument();
  const link = await screen.findByRole("link", { name: materialDto.technical_identity });
  expect(link).toHaveAttribute("href", "/materials/" + materialDto.id);
  await waitFor(() => expect(within(screen.getByRole("table")).getByText(processorDto.display_name)).toBeInTheDocument());
  const table = within(screen.getByRole("table"));
  for (const text of [materialDto.material_name, materialProject.name, materialBrand.name, "G03", "in progress", "not checked", "not published", "No"])
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
it.each([false, true])("handles API/network list failure (offline=%s) and retries", async (offline) => {
  backend({ failed: "/api/materials", failureStatus: 503, offline });
  render(<App initialPath="/materials" />);
  await screen.findByText("We couldn't load the data");
  backend();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("link", { name: materialDto.technical_identity })).toBeInTheDocument();
});
it.each([
  ["Search materials", "search", "Crystal"], ["Project", "project_id", materialProject.id],
  ["Published brand", "published_brand_id", materialBrand.id], ["Processor", "assigned_processor_id", processorDto.id],
  ["Main category", "main_category_code", "G03"], ["Workflow status", "workflow_status", "IN_PROGRESS"],
  ["Validation status", "validation_status", "NOT_CHECKED"], ["Publication status", "publication_status", "NOT_PUBLISHED"],
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
  for (const [label, value] of [["Search materials", "missing"], ["Project", materialProject.id], ["Published brand", materialBrand.id], ["Processor", processorDto.id], ["Main category", "G03"], ["Workflow status", "DONE"], ["Validation status", "VALID"], ["Publication status", "PUBLISHED_CURRENT"], ["Published", "true"]]) change(label, value);
  await waitFor(() => expect(queries.at(-1)?.size).toBe(9));
  expect(await screen.findByText("No materials found")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  await waitFor(() => expect(queries.at(-1)?.size).toBe(0));
  expect(await screen.findByRole("link", { name: materialDto.technical_identity })).toBeInTheDocument();
});
it("loads full detail including UUID, four-digit number, relations, path and states", async () => {
  backend(); render(<App initialPath={"/materials/" + materialDto.id} />);
  await screen.findByRole("heading", { name: materialDto.material_name });
  for (const text of [materialDto.id, "9999", "G03", "in progress", "not checked", "not published", processorDto.display_name]) expect(screen.getByText(text)).toBeInTheDocument();
  expect(screen.getAllByText("Not linked")).toHaveLength(2);
  expect(screen.getByRole("link", { name: materialProject.name })).toHaveAttribute("href", "/projects/" + materialProject.id);
  expect(screen.getByRole("link", { name: materialBrand.name })).toHaveAttribute("href", "/brands/" + materialBrand.id);
  expect(screen.getByText(materialDto.created_at)).toBeInTheDocument();
  expect(screen.getByText(materialDto.updated_at)).toBeInTheDocument();
  for (const name of ["Done", "Approve", "Publish", "Open folder"]) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
});
it.each([
  ["/api/projects/" + materialProject.id, "Project could not be loaded"],
  ["/api/internal-users/" + processorDto.id, "Processor could not be loaded"],
  ["/api/brands/" + materialBrand.id, "Published brand could not be loaded"],
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
it("creates with independent owners, active processors, first-field focus and exact POST", async () => {
  const { writes, fetchMock } = backend(); render(<App initialPath="/materials/new" />);
  expect(await screen.findByLabelText("Project *")).toHaveFocus();
  expect(screen.queryByRole("option", { name: inactiveDto.display_name })).not.toBeInTheDocument();
  await fillCreate(); submit();
  expect(await screen.findByRole("heading", { name: "New surface" })).toBeInTheDocument();
  expect(screen.getByText("Material created successfully.")).toHaveFocus();
  expect(materialBrand.company_id).not.toBe(materialProject.company_id);
  expect(fetchMock).toHaveBeenCalledWith("/api/internal-users?is_active=true", expect.any(Object));
  expect(writes).toEqual([{ path: "/api/materials", method: "POST", body: {
    project_id: materialProject.id, published_brand_id: materialBrand.id, material_name: "New surface",
    main_category_code: "G04", assigned_processor_id: processorDto.id,
  } }]);
});
it("edits with a minimal PATCH and shows managed fields read-only", async () => {
  const { writes } = backend(); render(<App initialPath={"/materials/" + materialDto.id + "/edit"} />);
  expect(await screen.findByLabelText("Material name *")).toHaveValue(materialDto.material_name);
  expect(screen.queryByLabelText("Published brand *")).not.toBeInTheDocument();
  expect(screen.getByText("Current record (read-only)")).toBeInTheDocument();
  change("Material name *", "Updated surface"); submit();
  await screen.findByText("Material updated successfully.");
  expect(writes).toEqual([{ path: "/api/materials/" + materialDto.id, method: "PATCH", body: { material_name: "Updated surface" } }]);
});
it("explains category conflict as a future rename and preserves input", async () => {
  backend({ write: async () => response({ detail: "main_category_code cannot be changed while folder_path is set." }, 409) });
  render(<App initialPath={"/materials/" + materialDto.id + "/edit"} />);
  await screen.findByLabelText("Main category *"); change("Main category *", "G04"); submit();
  expect(await screen.findByRole("alert")).toHaveTextContent("future rename operation");
  expect(screen.getByRole("alert")).toHaveFocus();
  expect(screen.getByLabelText("Main category *")).toHaveValue("G04");
  expect(screen.getByLabelText("Main category *")).toHaveAttribute("aria-describedby", "field-mainCategoryCode-error");
});
it("maps 422 to labelled fields and focuses summary", async () => {
  backend({ write: async () => response({ detail: [{ loc: ["body", "material_name"], msg: "Invalid material name" }] }, 422) });
  render(<App initialPath="/materials/new" />); await fillCreate(); submit();
  expect(await screen.findByRole("alert")).toHaveFocus();
  expect(screen.getByLabelText("Material name *")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByLabelText("Material name *")).toHaveAttribute("aria-describedby", "field-materialName-error");
  fireEvent.click(screen.getByRole("link", { name: "Invalid material name" }));
  expect(screen.getByLabelText("Material name *")).toHaveFocus();
});
it("blocks double submit while a write is pending", async () => {
  let finish: (r: Response) => void = () => {};
  const { writes } = backend({ write: () => new Promise((resolve) => { finish = resolve; }) });
  render(<App initialPath="/materials/new" />); await fillCreate();
  const form = screen.getByRole("form", { name: "Add material" });
  fireEvent.submit(form); fireEvent.submit(form);
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(screen.getByLabelText("Material name *")).toBeDisabled();
  await act(async () => finish(response(materialDto, 201)));
  await screen.findByText("Material created successfully.");
});
it("handles empty option lists without sending invalid create", async () => {
  const { writes } = backend({ emptyChoices: true }); render(<App initialPath="/materials/new" />);
  await screen.findByRole("form", { name: "Add material" }); submit();
  expect(screen.getAllByRole("alert")).toHaveLength(2);
  expect(writes).toHaveLength(0);
});
it("rejects malformed category locally", async () => {
  const { writes } = backend(); render(<App initialPath="/materials/new" />);
  await fillCreate(); change("Main category *", "../G03"); submit();
  expect(screen.getByRole("alert")).toHaveFocus();
  expect(writes).toHaveLength(0);
});
