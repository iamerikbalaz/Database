import { StrictMode } from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import App from "../App";
import { mockApiClient, type ApiClient } from "../api/client";
import { materialFromDto, type Material } from "../api/materialDto";
import type { Role } from "../auth/client";
import { SessionContext } from "../auth/context";
import { materialDto, processorDto } from "../test/materialFixtures";
import { DashboardPage } from "./DashboardPage";

const first = materialFromDto(materialDto);
const done: Material = { ...first, id: "50000000-0000-4000-8000-000000000002", technicalIdentity: "STONE_0002_A01",
  materialName: "Done sample", workflowStatus: "DONE", validationStatus: "VALID" };
const warning: Material = { ...done, id: "50000000-0000-4000-8000-000000000003", technicalIdentity: "STONE_0003_A01",
  materialName: "Warning sample", validationStatus: "WARNING" };
function tree(client: ApiClient, role: Role = "ADMIN", actor = processorDto.id, navigate = vi.fn()) {
  return <SessionContext.Provider value={{ session: { user: { ...processorDto, id: actor, role },
    csrf_token: "synthetic-dashboard-context", must_change_password: false }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <DashboardPage client={client} navigate={navigate} />
  </SessionContext.Provider>;
}
function api(rows: Material[] = [first, done, warning]) {
  return { ...mockApiClient, getMaterials: vi.fn().mockResolvedValue(rows) };
}

it("loads one server-scoped snapshot under StrictMode and keeps Done separate from publication", async () => {
  const client = api(); render(<StrictMode>{tree(client)}</StrictMode>);
  expect(screen.getByRole("button", { name: "Refresh overview" })).toBeDisabled();
  await screen.findByRole("button", { name: "All materials 3" });
  expect(client.getMaterials).toHaveBeenCalledExactlyOnceWith();
  fireEvent.click(screen.getByRole("button", { name: "Done 2" }));
  expect(screen.queryByRole("link", { name: first.technicalIdentity })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: done.technicalIdentity })).toBeInTheDocument();
  expect(screen.getByText(/Done does not mean approved or published/)).toBeInTheDocument();
  expect(within(screen.getByRole("list", { name: "Overview materials" })).getAllByText("not published")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Validation findings 1" }));
  expect(screen.queryByRole("link", { name: done.technicalIdentity })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: warning.technicalIdentity })).toBeInTheDocument();
  expect(client.getMaterials).toHaveBeenCalledTimes(1);
});

it("includes errors and missing metadata in findings without treating unchecked records as failures", async () => {
  const client = api([first, warning, { ...done, validationStatus: "ERROR" },
    { ...first, id: "50000000-0000-4000-8000-000000000004", technicalIdentity: "WOOD_0004_A01", validationStatus: "METADATA_MISSING" }]);
  render(tree(client)); fireEvent.click(await screen.findByRole("button", { name: "Validation findings 3" }));
  expect(screen.queryByRole("link", { name: first.technicalIdentity })).not.toBeInTheDocument();
  expect(screen.getAllByRole("listitem")).toHaveLength(3);
});

it.each(["ADMIN", "LEADERSHIP", "PRODUCTION_LEAD", "PROCESSOR"] as Role[])("shows only allowed shortcuts for %s", async (role) => {
  render(tree(api([]), role)); await screen.findByText("No materials available");
  expect(screen.queryAllByRole("link", { name: "Add material" })).toHaveLength(["ADMIN", "PRODUCTION_LEAD"].includes(role) ? 1 : 0);
  expect(screen.queryAllByRole("link", { name: "Prepare publication" })).toHaveLength(["ADMIN", "LEADERSHIP"].includes(role) ? 1 : 0);
  if (role === "PROCESSOR") expect(screen.getByText(/No active materials are assigned to you/)).toBeInTheDocument();
});

it("paginates without more requests, searches case-insensitively and opens the selected material", async () => {
  const rows = Array.from({ length: 23 }, (_, index) => ({ ...first, id: `50000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    technicalIdentity: `TEST_${String(index).padStart(4, "0")}_A01`, materialName: `Surface ${index}` }));
  const client = api(rows.reverse()); const navigate = vi.fn(); render(tree(client, "ADMIN", processorDto.id, navigate));
  await screen.findByText("Showing 1–10 of 23");
  expect(screen.getByRole("button", { name: "Previous materials" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Next materials" }));
  fireEvent.click(screen.getByRole("button", { name: "Next materials" }));
  expect(screen.getByText("Showing 21–23 of 23")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Next materials" })).toBeDisabled();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "  sUrFaCe 0 " } });
  expect(screen.getByText("Showing 1–1 of 1")).toBeInTheDocument();
  const link = screen.getByRole("link", { name: "TEST_0000_A01" });
  expect(link).toHaveAttribute("href", "/materials/50000000-0000-4000-8000-000000000000");
  fireEvent.click(link); expect(navigate).toHaveBeenCalledWith("/materials/50000000-0000-4000-8000-000000000000");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "TEST_0022" } });
  expect(screen.getByRole("link", { name: "TEST_0022_A01" })).toBeInTheDocument();
  expect(client.getMaterials).toHaveBeenCalledTimes(1);
});

it("replaces stale counts with an error after failed refresh, then recovers without showing zeroes", async () => {
  const client = api(); client.getMaterials.mockRejectedValueOnce(new Error("private diagnostic"));
  render(tree(client)); await screen.findByRole("alert");
  expect(screen.queryByRole("group", { name: "Material status filters" })).not.toBeInTheDocument();
  expect(screen.queryByText("private diagnostic")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Try again" })); await screen.findByRole("button", { name: "All materials 3" });
  client.getMaterials.mockRejectedValueOnce(new Error("private diagnostic"));
  fireEvent.click(screen.getByRole("button", { name: "Refresh overview" }));
  expect(screen.queryByRole("button", { name: "All materials 3" })).not.toBeInTheDocument();
  await screen.findByRole("alert"); expect(screen.queryByRole("list")).not.toBeInTheDocument();
  client.getMaterials.mockResolvedValueOnce([first]);
  fireEvent.click(screen.getByRole("button", { name: "Try again" })); await screen.findByRole("button", { name: "All materials 1" });
});

it.each(["actor", "role"])("discards prior data and late responses when the %s changes", async (change) => {
  let finish!: (rows: Material[]) => void;
  const client = api(); client.getMaterials.mockImplementationOnce(() => new Promise<Material[]>((resolve) => { finish = resolve; }));
  const mounted = render(tree(client));
  await act(async () => {});
  client.getMaterials.mockResolvedValueOnce([first]);
  mounted.rerender(tree(client, change === "role" ? "PROCESSOR" : "ADMIN", change === "actor" ? done.id : processorDto.id));
  await screen.findByRole("button", { name: "All materials 1" });
  await act(async () => { finish([done, warning]); });
  expect(screen.queryByRole("link", { name: done.technicalIdentity })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "All materials 1" })).toBeInTheDocument();
});

it("resets an existing account's filters and rendered data immediately on account change", async () => {
  const client = api(); const mounted = render(tree(client));
  fireEvent.click(await screen.findByRole("button", { name: "Done 2" }));
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Done sample" } });
  client.getMaterials.mockResolvedValueOnce([first]); mounted.rerender(tree(client, "PROCESSOR", done.id));
  expect(screen.queryByRole("link", { name: done.technicalIdentity })).not.toBeInTheDocument();
  await screen.findByRole("button", { name: "All materials 1" });
  expect(screen.getByRole("searchbox")).toHaveValue("");
  expect(screen.getByRole("button", { name: "All materials 1" })).toHaveAttribute("aria-pressed", "true");
});

it.each(["/", "/dashboard"])("serves the working overview at %s", async (path) => {
  render(<App client={api([first])} initialPath={path} />);
  await screen.findByRole("button", { name: "All materials 1" });
  expect(screen.queryByText(/overview is coming next/)).not.toBeInTheDocument();
});

it("renders untrusted material text inertly and never displays source paths", async () => {
  render(tree(api([{ ...first, materialName: '<img src=x onerror="alert(1)">', folderPath: "private/source/location" }])));
  await screen.findByText('<img src=x onerror="alert(1)">');
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.queryByText("private/source/location")).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "does not match" } });
  expect(screen.getByText("No matching materials")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "All materials 1" })).toBeInTheDocument();
});
