import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { mockApiClient } from "./api/client";
import { catalogClient } from "./api/catalogClient";
import { materialFromDto } from "./api/materialDto";
import type { MaterialFilters } from "./api/materialClient";
import { SessionContext, type SessionContextValue } from "./auth/context";
import { setSessionToken } from "./auth/sessionTransport";
import { materialDto, processorDto } from "./test/materialFixtures";

vi.mock("./pages/MaterialDetailPage", () => ({
  MaterialDetailPage: ({ navigate, includeArchived }: { navigate: (path: string) => void; includeArchived?: boolean }) =>
    <section><h1>Material card</h1><button onClick={() => navigate(includeArchived ? "/material-archives" : "/materials")}>Back to materials</button></section>,
}));

const first = materialFromDto(materialDto);
const second = { ...first, id: "50000000-0000-4000-8000-000000000002", materialName: "Second material" };
const archived = { ...first, id: "50000000-0000-4000-8000-000000000003", materialName: "Archived sample", isArchived: true };
const auth: SessionContextValue = { session: { user: { ...processorDto, role: "ADMIN" }, csrf_token: "t".repeat(43), must_change_password: false }, pending: false, logout: vi.fn(), changePassword: vi.fn() };

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState({}, "", "/materials");
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  vi.spyOn(catalogClient, "categories").mockResolvedValue([]);
});
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); setSessionToken(null); window.history.replaceState({}, "", "/"); });

function setup() {
  let records = [first, second];
  const getMaterials = vi.fn(async (filters: MaterialFilters) => filters.is_archived ? [archived] : records);
  const client = { ...mockApiClient, getMaterials };
  const tree = (session = auth) => <SessionContext.Provider value={session}><App client={client} /></SessionContext.Provider>;
  const rendered = render(tree());
  return { getMaterials, changeRecords: (next: typeof records) => { records = next; }, rerender: (session = auth) => rendered.rerender(tree(session)), unmount: rendered.unmount, tree };
}

async function browserBack() {
  await act(async () => {
    const moved = new Promise<void>(resolve => window.addEventListener("popstate", () => resolve(), { once: true }));
    window.history.back(); await moved;
  });
}

it.each(["card link", "browser back"])("restores checked rows and filters through %s without requiring Keep filters", async method => {
  const { getMaterials } = setup();
  await screen.findByRole("table");
  fireEvent.change(screen.getByRole("searchbox", { name: "Search materials" }), { target: { value: "#review" } });
  await waitFor(() => expect(getMaterials).toHaveBeenLastCalledWith({ search: "#review" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: `Select ${first.materialName}` }));
  fireEvent.click(screen.getByRole("checkbox", { name: `Select ${second.materialName}` }));
  expect(screen.getByRole("checkbox", { name: "Keep filters" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("link", { name: first.materialName }));
  await screen.findByRole("heading", { name: "Material card" });
  if (method === "browser back") await browserBack();
  else fireEvent.click(screen.getByRole("button", { name: "Back to materials" }));
  expect(await screen.findByRole("checkbox", { name: `Select ${first.materialName}` })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: `Select ${second.materialName}` })).toBeChecked();
  expect(screen.getByRole("searchbox", { name: "Search materials" })).toHaveValue("#review");
  expect(getMaterials).toHaveBeenLastCalledWith({ search: "#review" });
  expect(screen.getByRole("group", { name: "Apply to 2 selected materials" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(await screen.findByRole("checkbox", { name: `Select ${first.materialName}` })).not.toBeChecked();
});

it("discards removed or no-longer-visible IDs after loading fresh data on return", async () => {
  const { changeRecords } = setup();
  fireEvent.click(await screen.findByRole("checkbox", { name: `Select ${first.materialName}` }));
  fireEvent.click(screen.getByRole("checkbox", { name: `Select ${second.materialName}` }));
  fireEvent.click(screen.getByRole("link", { name: first.materialName }));
  changeRecords([second]);
  fireEvent.click(await screen.findByRole("button", { name: "Back to materials" }));
  expect(await screen.findByRole("checkbox", { name: `Select ${second.materialName}` })).toBeChecked();
  expect(screen.queryByRole("checkbox", { name: `Select ${first.materialName}` })).not.toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Apply to 1 selected materials" })).toBeVisible();
  changeRecords([first, second]);
  fireEvent.click(screen.getByRole("button", { name: "Refresh materials" }));
  expect(await screen.findByRole("checkbox", { name: `Select ${first.materialName}` })).not.toBeChecked();
});

it("keeps active and archive selections separate", async () => {
  setup();
  fireEvent.click(await screen.findByRole("checkbox", { name: `Select ${first.materialName}` }));
  fireEvent.click(screen.getByRole("link", { name: "Archived materials" }));
  expect(await screen.findByRole("checkbox", { name: `Select ${archived.materialName}` })).not.toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: `Select ${archived.materialName}` }));
  fireEvent.click(screen.getByRole("link", { name: archived.materialName }));
  fireEvent.click(await screen.findByRole("button", { name: "Back to materials" }));
  expect(await screen.findByRole("checkbox", { name: `Select ${archived.materialName}` })).toBeChecked();
  fireEvent.click(screen.getByRole("link", { name: "Materials" }));
  expect(await screen.findByRole("checkbox", { name: `Select ${first.materialName}` })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: `Select ${second.materialName}` })).not.toBeChecked();
});

it.each(["account", "session", "reload"])("clears in-memory selection and temporary filters on %s change", async kind => {
  const app = setup();
  await screen.findByRole("table");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "#private" } });
  fireEvent.click(await screen.findByRole("checkbox", { name: `Select ${first.materialName}` }));
  if (kind === "account") app.rerender({ ...auth, session: { ...auth.session, user: { ...auth.session.user, id: "40000000-0000-4000-8000-000000000099" } } });
  else if (kind === "session") { setSessionToken("n".repeat(43)); app.rerender(); }
  else { app.unmount(); render(app.tree()); }
  expect(await screen.findByRole("checkbox", { name: `Select ${first.materialName}` })).not.toBeChecked();
  expect(screen.getByRole("searchbox")).toHaveValue("");
  expect(localStorage.getItem(`reawote-filters:${processorDto.id}:materials:v1`)).toBeNull();
});
