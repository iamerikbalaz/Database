import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ProjectsPage } from "./ProjectsPage";
import { CompaniesPage } from "./CompaniesPage";
import { mockApiClient } from "../api/client";
import { SessionContext } from "../auth/context";
import { processorDto } from "../test/materialFixtures";
import { companies, projects } from "../api/mockData";
import type { ReactNode } from "react";

function manager(children: ReactNode) { return <SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>{children}</SessionContext.Provider>; }
beforeEach(() => { localStorage.clear(); HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); }; HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); }; });
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });
it("searches project notes and links folders, then bulk changes only the filtered revision", async () => {
  const project = { ...projects[0], description: "#fabrics project", folderPath: "R:\\0. PROJECTS\\0246_EXAMPLE_SCANNING_FABRICS_032026" };
  const updateProject = vi.fn().mockResolvedValue({ ...project, status: "done", updatedAt: "2026-09-26T00:00:00Z" });
  const client = { ...mockApiClient, getProjects: vi.fn().mockResolvedValue([project, { ...projects[1], description: null }]), updateProject };
  render(manager(<ProjectsPage client={client} navigate={vi.fn()} />));
  await screen.findByRole("link", { name: project.name });
  expect(screen.getByText(project.folderPath)).toBeVisible();
  fireEvent.change(screen.getByRole("textbox", { name: "Search projects" }), { target: { value: "#fabrics" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Bulk property" }), { target: { value: "status" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Bulk value" }), { target: { value: "DONE" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply to all 1 filtered" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Confirm changes" }));
  await waitFor(() => expect(updateProject).toHaveBeenCalledWith(project.id, { status: "DONE", expected_updated_at: project.updatedAt }, expect.any(String)));
  expect(updateProject).toHaveBeenCalledTimes(1);
});
it("filters companies by activity and edits a cell with the exact database revision", async () => {
  const company = { ...companies[0], country: "Czech Republic" };
  const updateCompany = vi.fn().mockResolvedValue({ ...company, status: "inactive", updatedAt: "2026-09-26T00:00:00Z" });
  const client = { ...mockApiClient, getCompanies: vi.fn().mockResolvedValue([company, { ...companies[1], status: "inactive" }]), updateCompany };
  render(manager(<CompaniesPage client={client} navigate={vi.fn()} />));
  await screen.findByRole("link", { name: company.name });
  fireEvent.change(screen.getByRole("combobox", { name: "Filter company activity" }), { target: { value: "active" } });
  expect(screen.queryByRole("link", { name: companies[1].name })).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: `Active for ${company.name}` }), { target: { value: "false" } });
  await screen.findByText("Saved. Refresh to reapply filters.");
  expect(updateCompany).toHaveBeenCalledWith(company.id, { is_active: false, expected_updated_at: company.updatedAt }, expect.any(String));
});
