import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { companies, brands, projects } from "./api/mockData";
import { companyToDto, publishedBrandToDto, projectToDto } from "./api/dto";

const company = companyToDto(companies[0]);
const brand = publishedBrandToDto(brands[0]);
const project = projectToDto(projects[0]);
const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
type Write = { path: string; method: string; body: Record<string, unknown> };
function backend(writeResult?: (write: Write) => Promise<Response>) {
  let companyRecord = { ...company },
    brandRecord = { ...brand },
    projectRecord = { ...project };
  const writes: Write[] = [];
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method !== "GET") {
      const body: Record<string, unknown> = JSON.parse(String(init?.body));
      const write = { path, method, body };
      writes.push(write);
      if (writeResult) return writeResult(write);
      if (path.startsWith("/api/companies")) {
        companyRecord = { ...companyRecord, ...body };
        return response(companyRecord, method === "POST" ? 201 : 200);
      }
      if (path.startsWith("/api/brands")) {
        brandRecord = { ...brandRecord, ...body };
        return response(brandRecord, method === "POST" ? 201 : 200);
      }
      projectRecord = { ...projectRecord, ...body };
      return response(projectRecord, method === "POST" ? 201 : 200);
    }
    const data: Record<string, unknown> = {
      "/api/companies": [companyRecord],
      ["/api/companies/" + company.id]: companyRecord,
      "/api/brands": [brandRecord],
      ["/api/brands/" + brand.id]: brandRecord,
      "/api/projects": [projectRecord],
      ["/api/projects/" + project.id]: projectRecord,
    };
    return path in data
      ? response(data[path])
      : response({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { writes, fetchMock };
}
const fill = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
async function save() {
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
}
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("HTTP forms", () => {
  it("creates a company using only writable snake_case fields and navigates to detail", async () => {
    const { writes, fetchMock } = backend();
    render(<App initialPath="/companies/new" />);
    const name = await screen.findByLabelText("Name *");
    expect(name).toHaveFocus();
    fill("Name *", "  New company  ");
    fill("Legal name", "New Company Ltd");
    fill("Website", "https://example.com/");
    await save();
    expect(
      await screen.findByRole("heading", { name: "New company" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Company created successfully.")).toHaveFocus();
    expect(writes).toEqual([
      {
        path: "/api/companies",
        method: "POST",
        body: {
          name: "New company",
          legal_name: "New Company Ltd",
          country: null,
          address: null,
          website: "https://example.com/",
          vat_id: null,
          notion_page_id: null,
          is_active: true,
        },
      },
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/companies",
      expect.objectContaining({
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
      }),
    );
  });
  it("validates required and URL fields before sending and focuses the error summary", async () => {
    const { writes } = backend();
    render(<App initialPath="/companies/new" />);
    await screen.findByLabelText("Name *");
    fill("Name *", "   ");
    fill("Website", "javascript:alert(1)");
    await save();
    expect(screen.getByRole("alert")).toHaveFocus();
    expect(screen.getByLabelText("Name *")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(screen.getByLabelText("Website")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(writes).toHaveLength(0);
  });
  it("keeps user input on 409 and allows correction and retry", async () => {
    let conflict = true;
    const { writes } = backend(async (write) => {
      if (conflict) {
        conflict = false;
        return response({ detail: "notion_page_id already exists." }, 409);
      }
      return response({ ...company, ...write.body }, 201);
    });
    render(<App initialPath="/companies/new" />);
    await screen.findByLabelText("Name *");
    fill("Name *", "New company");
    fill("Notion page ID", "duplicate");
    await save();
    expect(await screen.findByRole("alert")).toHaveTextContent("already used");
    expect(screen.getByLabelText("Name *")).toHaveValue("New company");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    fill("Notion page ID", "unique");
    await save();
    await screen.findByText("Company created successfully.");
    expect(writes).toHaveLength(2);
  });
  it("maps backend 422 issues onto accessible form fields", async () => {
    backend(async () =>
      response(
        {
          detail: [
            {
              loc: ["body", "website"],
              msg: "URL is invalid",
              type: "url_parsing",
            },
          ],
        },
        422,
      ),
    );
    render(<App initialPath="/companies/new" />);
    await screen.findByLabelText("Name *");
    fill("Name *", "Company");
    await save();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "URL is invalid",
    );
    expect(screen.getByLabelText("Website")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    fireEvent.click(screen.getByRole("link", { name: "URL is invalid" }));
    expect(screen.getByLabelText("Website")).toHaveFocus();
  });
  it("edits a company with a minimal PATCH and clears optional fields with null", async () => {
    const { writes } = backend();
    render(<App initialPath={"/companies/" + company.id + "/edit"} />);
    expect(await screen.findByLabelText("Name *")).toHaveValue(company.name);
    fill("Legal name", "");
    fill("Name *", "Renamed company");
    await save();
    await screen.findByRole("heading", { name: "Renamed company" });
    expect(writes).toEqual([
      {
        path: "/api/companies/" + company.id,
        method: "PATCH",
        body: { name: "Renamed company", legal_name: null },
      },
    ]);
  });
  it("creates a published brand owned by the route company without a sequence counter", async () => {
    const { writes } = backend();
    render(<App initialPath={"/companies/" + company.id + "/brands/new"} />);
    await screen.findByLabelText("Name *");
    expect(screen.getByLabelText("Company *")).toBeDisabled();
    expect(screen.getByLabelText("Company *")).toHaveValue(company.id);
    fill("Name *", "New brand");
    fill("Folder prefix *", "NEW");
    fill("Brand identifier *", "new-brand");
    await save();
    await screen.findByRole("heading", { name: "New brand" });
    expect(writes).toEqual([
      {
        path: "/api/brands",
        method: "POST",
        body: {
          company_id: company.id,
          name: "New brand",
          folder_prefix: "NEW",
          brand_identifier: "new-brand",
          is_active: true,
        },
      },
    ]);
  });
  it("edits a published brand without writing system-managed fields", async () => {
    const { writes } = backend();
    render(<App initialPath={"/brands/" + brand.id + "/edit"} />);
    await screen.findByLabelText("Name *");
    fill("Brand identifier *", "updated-brand");
    await save();
    await screen.findByText("Published brand updated successfully.");
    expect(writes[0]).toEqual({
      path: "/api/brands/" + brand.id,
      method: "PATCH",
      body: { brand_identifier: "updated-brand" },
    });
  });
  it("creates a project with explicit owner and backend status", async () => {
    const { writes } = backend();
    render(<App initialPath="/projects/new" />);
    await screen.findByLabelText("Name *");
    fill("Company *", company.id);
    fill("Name *", "New project");
    fill("Project number *", "PRJ-NEW");
    fill("Status *", "in_progress");
    fill("Due date", "2026-12-01");
    fill("Notes", "Project notes");
    await save();
    await screen.findByRole("heading", { name: "New project" });
    expect(writes).toEqual([
      {
        path: "/api/projects",
        method: "POST",
        body: {
          company_id: company.id,
          name: "New project",
          project_number: "PRJ-NEW",
          status: "IN_PROGRESS",
          due_date: "2026-12-01",
          notes: "Project notes",
        },
      },
    ]);
  });
  it("edits a project and clears its due date using PATCH", async () => {
    const { writes } = backend();
    render(<App initialPath={"/projects/" + project.id + "/edit"} />);
    await screen.findByLabelText("Name *");
    fill("Due date", "");
    fill("Status *", "done");
    await save();
    await screen.findByText("Project updated successfully.");
    expect(writes[0]).toEqual({
      path: "/api/projects/" + project.id,
      method: "PATCH",
      body: { status: "DONE", due_date: null },
    });
  });
  it("prevents duplicate submissions while the POST is pending", async () => {
    let finish: (response: Response) => void = () => {};
    const { writes } = backend(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(<App initialPath="/companies/new" />);
    await screen.findByLabelText("Name *");
    fill("Name *", "Pending");
    const form = screen.getByRole("form", { name: "Add company" });
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(screen.getByLabelText("Name *")).toBeDisabled();
    await act(async () =>
      finish(response({ ...company, name: "Pending" }, 201)),
    );
    await screen.findByText("Company created successfully.");
  });
  it("renders a load error instead of an editable blank form for a missing record", async () => {
    const { writes } = backend();
    render(<App initialPath="/companies/missing/edit" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "could not be loaded",
    );
    expect(
      screen.queryByRole("button", { name: "Save" }),
    ).not.toBeInTheDocument();
    expect(writes).toHaveLength(0);
  });
  it("activates the Add company entry point", async () => {
    backend();
    render(<App initialPath="/companies" />);
    fireEvent.click(screen.getByRole("link", { name: "Add company" }));
    expect(
      await screen.findByRole("form", { name: "Add company" }),
    ).toBeInTheDocument();
  });
});

it("does not navigate away from a different page when a pending save finishes", async () => {
  let finish: (response: Response) => void = () => {};
  backend(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<App initialPath="/companies/new" />);
  await screen.findByLabelText("Name *");
  fill("Name *", "Pending");
  await save();
  fireEvent.click(screen.getByRole("link", { name: "Projects" }));
  await screen.findByRole("heading", { name: "Projects" });
  await act(async () => finish(response(company, 201)));
  expect(screen.getByRole("heading", { name: "Projects" })).toBeInTheDocument();
  expect(
    screen.queryByText("Company created successfully."),
  ).not.toBeInTheDocument();
});
it("does not save an overlong company name or a project without an owner", async () => {
  const { writes } = backend();
  const { unmount } = render(<App initialPath="/companies/new" />);
  await screen.findByLabelText("Name *");
  fill("Name *", "x".repeat(256));
  await save();
  expect(screen.getByRole("alert")).toHaveTextContent("at most 255");
  unmount();
  render(<App initialPath="/projects/new" />);
  await screen.findByLabelText("Name *");
  fill("Name *", "Project");
  fill("Project number *", "P-1");
  await save();
  expect(screen.getByRole("alert")).toHaveTextContent("Company is required");
  expect(writes).toHaveLength(0);
});
