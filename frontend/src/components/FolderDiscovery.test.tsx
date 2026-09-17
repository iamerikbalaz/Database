import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FolderDiscovery } from "./FolderDiscovery";
import { discoveryClient } from "../api/discoveryClient";
import { SessionContext } from "../auth/context";
import { materialDto, processorDto } from "../test/materialFixtures";
import type { Role } from "../auth/client";

const identity = materialDto.technical_identity;
const result = (parentPath = "") => ({ parentPath, omittedEntries: 2, directories: [
  { name: "Library", path: (parentPath ? parentPath + "/" : "") + "Library", identityMatches: false },
  { name: identity, path: (parentPath ? parentPath + "/" : "") + identity, identityMatches: true },
] });
beforeEach(() => { vi.spyOn(discoveryClient, "listing").mockImplementation(async (_id, _identity, parent) => result(parent)); });
afterEach(() => vi.restoreAllMocks());
function mount(role: Role = "ADMIN", disabled = false) {
  const select = vi.fn();
  const rendered = render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <FolderDiscovery materialId={materialDto.id} identity={identity} disabled={disabled} onSelect={select} />
  </SessionContext.Provider>);
  return { ...rendered, select };
}
function open() { fireEvent.click(screen.getByRole("button", { name: "Browse source folders" })); }
async function list() { fireEvent.click(screen.getByRole("button", { name: "List folders" })); await screen.findByText(/2 folders in/); }

it.each(["PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP"] as const)("hides the source browser from %s", (role) => {
  mount(role); expect(screen.queryByRole("button")).not.toBeInTheDocument(); expect(discoveryClient.listing).not.toHaveBeenCalled();
});
it("only reads after explicit listing and selects only an exact identity match", async () => {
  const { select } = mount(); expect(discoveryClient.listing).not.toHaveBeenCalled(); open(); expect(discoveryClient.listing).not.toHaveBeenCalled();
  await list(); expect(discoveryClient.listing).toHaveBeenCalledWith(materialDto.id, identity, "");
  expect(screen.getByText(/2 files or entries/)).toBeVisible(); expect(screen.getAllByRole("button", { name: "Use this folder" })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Use this folder" }));
  expect(select).toHaveBeenCalledWith(identity); expect(screen.queryByLabelText("Parent relative path")).not.toBeInTheDocument();
});
it("opens one child and returns to its parent without selecting a folder", async () => {
  const { select } = mount(); open(); await list(); fireEvent.click(screen.getByRole("button", { name: "Open folder Library" }));
  await screen.findByText("2 folders in Library.");
  fireEvent.click(screen.getByRole("button", { name: "Parent folder" })); await screen.findByText("2 folders in source root.");
  expect(select).not.toHaveBeenCalled(); expect(discoveryClient.listing).toHaveBeenCalledTimes(3);
});
it("clears results when the parent input changes and rejects invalid paths locally", async () => {
  mount(); open(); await list(); fireEvent.change(screen.getByLabelText("Parent relative path"), { target: { value: "../private" } });
  expect(screen.queryByRole("button", { name: "Use this folder" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "List folders" })); expect(await screen.findByRole("alert")).not.toHaveTextContent("private");
  expect(discoveryClient.listing).toHaveBeenCalledOnce();
});
it("bounds displayed entries with paging and resets the page when filtered", async () => {
  vi.mocked(discoveryClient.listing).mockResolvedValue({ parentPath: "", omittedEntries: 0, directories: Array.from({ length: 51 }, (_, index) => ({ name: `Folder ${index}`, path: `Folder ${index}`, identityMatches: false })) });
  mount(); open(); fireEvent.click(screen.getByRole("button", { name: "List folders" })); await screen.findByText("51 folders in source root.");
  expect(screen.getAllByRole("listitem")).toHaveLength(50); fireEvent.click(screen.getByRole("button", { name: "Next folders" })); expect(screen.getAllByRole("listitem")).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("Filter these folders"), { target: { value: "Folder 0" } }); expect(screen.getByText("Folder 0")).toBeVisible();
});
it("shows a safe error and permits retry", async () => {
  vi.mocked(discoveryClient.listing).mockRejectedValueOnce(new Error("PRIVATE_SYNTHETIC")); mount(); open();
  fireEvent.click(screen.getByRole("button", { name: "List folders" })); expect(await screen.findByRole("alert")).not.toHaveTextContent("PRIVATE_SYNTHETIC");
  await list(); expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
it("prevents duplicate reads and ignores a result after unmount", async () => {
  let resolve!: (value: ReturnType<typeof result>) => void;
  vi.mocked(discoveryClient.listing).mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  const view = mount(); open(); fireEvent.click(screen.getByRole("button", { name: "List folders" }));
  fireEvent.submit(screen.getByRole("form", { name: "Browse parent folder" }));
  await waitFor(() => expect(discoveryClient.listing).toHaveBeenCalledOnce()); view.unmount(); await act(async () => resolve(result())); expect(view.select).not.toHaveBeenCalled();
});
it("blocks browsing while a material operation is pending", () => { mount("ADMIN", true); expect(screen.getByRole("button")).toBeDisabled(); });
