import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { mockApiClient, type ApiClient } from "./api/client";
import { companies } from "./api/mockData";
import { CompaniesPage } from "./pages/CompaniesPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { CompanyDetailPage } from "./pages/CompanyDetailPage";

Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
  configurable: true,
  writable: true,
  value() {},
});
Object.defineProperty(HTMLDialogElement.prototype, "close", {
  configurable: true,
  writable: true,
  value() {},
});
beforeEach(() => {
  // jsdom has no native dialog implementation; emulate the browser methods for component tests.
  vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(
    function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
      this.querySelector<HTMLButtonElement>("button")?.focus();
    },
  );
  vi.spyOn(HTMLDialogElement.prototype, "close").mockImplementation(function (
    this: HTMLDialogElement,
  ) {
    this.removeAttribute("open");
    this.dispatchEvent(new Event("close"));
  });
});
afterEach(() => vi.restoreAllMocks());

describe("frontend regression coverage", () => {
  it("shows projects and all supported status labels", async () => {
    render(<App client={mockApiClient} initialPath="/projects" />);
    expect(
      await screen.findByRole("link", { name: "Swisspearl facade collection" }),
    ).toBeInTheDocument();
    for (const label of ["Not started", "In progress", "Done"])
      expect(screen.getByText(label)).toBeInTheDocument();
  });
  it("shows full company detail and related records", async () => {
    render(
      <App
        client={mockApiClient}
        initialPath="/companies/10000000-0000-4000-8000-000000000001"
      />,
    );
    expect(
      await screen.findByRole("heading", { name: "Swisspearl" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/swisspearl · SWISSPEARL · Next: 1/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Swisspearl facade collection/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Crystal lighting materials"),
    ).not.toBeInTheDocument();
  });
  it("shows project details", async () => {
    render(
      <App
        client={mockApiClient}
        initialPath="/projects/20000000-0000-4000-8000-000000000001"
      />,
    );
    expect(
      await screen.findByRole("heading", {
        name: "Swisspearl facade collection",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("2026-09-18")).toBeInTheDocument();
    expect(screen.getByText("2026-06-12T00:00:00Z")).toBeInTheDocument();
  });
  it.each(["/companies/missing", "/projects/missing"])(
    "handles missing detail %s",
    async (path) => {
      render(<App client={mockApiClient} initialPath={path} />);
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "could not be found or loaded",
      );
    },
  );
  it.each(["/companies/%", "/projects/%E0%A4%A", "/%ZZ"])(
    "handles malformed URL %s without requesting data",
    (path) => {
      const getCompany = vi.fn(),
        getProject = vi.fn();
      render(
        <App
          client={{ ...mockApiClient, getCompany, getProject }}
          initialPath={path}
        />,
      );
      expect(
        screen.getByRole("heading", { name: "Page not found" }),
      ).toBeInTheDocument();
      expect(getCompany).not.toHaveBeenCalled();
      expect(getProject).not.toHaveBeenCalled();
    },
  );
  it("shows a controlled error when a company detail dependency fails", async () => {
    render(
      <App
        client={{
          ...mockApiClient,
          getCompany: vi.fn().mockRejectedValue(new Error("brands failed")),
        }}
        initialPath="/companies/10000000-0000-4000-8000-000000000001"
      />,
    );
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Published brands" }),
    ).not.toBeInTheDocument();
  });
  it("navigates once from a company table button", async () => {
    const navigate = vi.fn();
    render(<CompaniesPage client={mockApiClient} navigate={navigate} />);
    fireEvent.click(await screen.findByRole("link", { name: "Swisspearl" }));
    expect(navigate).toHaveBeenCalledExactlyOnceWith(
      "/companies/10000000-0000-4000-8000-000000000001",
    );
  });
  it("navigates once from a project table button", async () => {
    const navigate = vi.fn();
    render(<ProjectsPage client={mockApiClient} navigate={navigate} />);
    fireEvent.click(
      await screen.findByRole("link", { name: "Swisspearl facade collection" }),
    );
    expect(navigate).toHaveBeenCalledExactlyOnceWith(
      "/projects/20000000-0000-4000-8000-000000000001",
    );
  });
  it("opens accessible mobile navigation and restores focus after Escape", () => {
    render(
      <App
        client={mockApiClient}
        initialPath="/companies/10000000-0000-4000-8000-000000000001"
      />,
    );
    const trigger = screen.getByRole("button", { name: "Open navigation" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Companies" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Navigation" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(
      within(dialog).getByRole("button", { name: "Close navigation" }),
    ).toHaveFocus();
    expect(
      within(dialog).getByRole("link", { name: "Companies" }),
    ).toHaveAttribute("aria-current", "page");
    // Escape dispatches cancel on a native modal dialog.
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
  it("closes mobile navigation after selecting a destination", () => {
    render(<App client={mockApiClient} initialPath="/dashboard" />);
    const trigger = screen.getByRole("button", { name: "Open navigation" });
    fireEvent.click(trigger);
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("link", {
        name: "Projects",
      }),
    );
    expect(
      screen.getByRole("heading", { name: "Projects" }),
    ).toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });
  it("disables unfinished actions", async () => {
    const { unmount } = render(
      <App client={mockApiClient} initialPath="/companies" />,
    );
    expect(screen.getByRole("link", { name: "Add company" })).toHaveAttribute(
      "href",
      "/companies/new",
    );
    expect(screen.getByRole("button", { name: "User menu" })).toBeDisabled();
    unmount();
    render(
      <App
        client={mockApiClient}
        initialPath="/projects/20000000-0000-4000-8000-000000000001"
      />,
    );
    expect(
      await screen.findByRole("link", { name: "Edit project" }),
    ).toHaveAttribute(
      "href",
      "/projects/20000000-0000-4000-8000-000000000001/edit",
    );
  });
  it("ignores a late detail response after the ID changes", async () => {
    let finish: (
      value: Awaited<ReturnType<ApiClient["getCompany"]>>,
    ) => void = () => {};
    const getCompany = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      )
      .mockResolvedValueOnce({ ...companies[1], brands: [], projects: [] });
    const client = { ...mockApiClient, getCompany };
    const { rerender } = render(
      <CompanyDetailPage
        id="10000000-0000-4000-8000-000000000001"
        client={client}
        navigate={vi.fn()}
      />,
    );
    await waitFor(() => expect(getCompany).toHaveBeenCalledTimes(1));
    rerender(
      <CompanyDetailPage
        id="10000000-0000-4000-8000-000000000002"
        client={client}
        navigate={vi.fn()}
      />,
    );
    expect(
      await screen.findByRole("heading", { name: "Lasvit" }),
    ).toBeInTheDocument();
    await act(async () =>
      finish({ ...companies[0], brands: [], projects: [] }),
    );
    expect(screen.getByRole("heading", { name: "Lasvit" })).toBeInTheDocument();
  });
  it("renders lists and details without React console warnings", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    render(<App client={mockApiClient} initialPath="/companies" />);
    fireEvent.click(await screen.findByRole("link", { name: "Swisspearl" }));
    expect(
      await screen.findByRole("heading", { name: "Swisspearl" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("link", { name: /Swisspearl facade collection/ }),
    );
    expect(
      await screen.findByRole("heading", {
        name: "Swisspearl facade collection",
      }),
    ).toBeInTheDocument();
    expect(error).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
  });
});

it("renders an empty project list", async () => {
  render(
    <App
      client={{ ...mockApiClient, getProjects: async () => [] }}
      initialPath="/projects"
    />,
  );
  expect(
    await screen.findByRole("heading", { name: "No projects yet" }),
  ).toBeInTheDocument();
});
it("renders an empty company detail without undefined list access", async () => {
  render(
    <App
      client={{
        ...mockApiClient,
        getCompany: async () => ({ ...companies[0], brands: [], projects: [] }),
      }}
      initialPath={"/companies/" + companies[0].id}
    />,
  );
  expect(await screen.findByText("No published brands.")).toBeInTheDocument();
  expect(screen.getByText("No projects for this company.")).toBeInTheDocument();
});
