import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PathsSettingsPage } from "./PathsSettingsPage";
import { pathSettingsClient as api } from "../api/pathSettingsClient";
import { SessionContext } from "../auth/context";
import { processorDto } from "../test/materialFixtures";
import { ApiError } from "../api/errors";
import { requestNavigation } from "../navigationGuard";
import { setSessionToken } from "../auth/sessionTransport";

const initial = { version: 0, sbsTemplatesRoot: "C:\\Templates", ordersRoot: "R:\\0. PROJECTS", materialsRoot: "C:\\Test_data", publishedLibraryRoot: "Z:\\Published", canSelectFolder: true };
afterEach(() => { vi.restoreAllMocks(); setSessionToken(null); });
function mount(role: "ADMIN" | "PROCESSOR" = "ADMIN") {
  return render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}><PathsSettingsPage navigate={vi.fn()} /></SessionContext.Provider>);
}
it("saves versioned paths and recovers a lost reply with the exact frozen request", async () => {
  vi.spyOn(api, "current").mockResolvedValue(initial);
  const save = vi.spyOn(api, "save").mockRejectedValueOnce(new TypeError("lost reply")).mockResolvedValue({ ...initial, version: 1, sbsTemplatesRoot: "C:\\NewTemplates" });
  mount(); fireEvent.change(await screen.findByLabelText("SBS templates folder"), { target: { value: "C:\\NewTemplates" } });
  fireEvent.click(screen.getByRole("button", { name: "Save paths" }));
  fireEvent.click(await screen.findByRole("button", { name: "Recover same paths request" }));
  await screen.findByText(/Paths saved/);
  expect(save.mock.calls[0]).toEqual(save.mock.calls[1]);
  expect(save.mock.calls[0][0]).toMatchObject({ expected_version: 0, sbs_templates_root: "C:\\NewTemplates", published_library_root: "Z:\\Published" });
  expect(requestNavigation("/materials")).toBe(true);
});
it("surfaces rejected folders without overwriting the draft and offers reload", async () => {
  vi.spyOn(api, "current").mockResolvedValue(initial);
  vi.spyOn(api, "save").mockRejectedValue(new ApiError(422, "Folder is not available."));
  mount(); fireEvent.change(await screen.findByLabelText("Orders root"), { target: { value: "C:\\Missing" } });
  fireEvent.click(screen.getByRole("button", { name: "Save paths" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Folder is not available.");
  expect(screen.getByLabelText("Orders root")).toHaveValue("C:\\Missing");
  expect(screen.getByRole("button", { name: "Reload paths" })).toBeEnabled();
  expect(requestNavigation("/materials")).toBe(true);
});
it("does not expose path editors to a processor", async () => {
  vi.spyOn(api, "current").mockRejectedValue(new ApiError(403, "Forbidden"));
  mount("PROCESSOR");
  expect(screen.getByText(/Only an administrator/)).toBeVisible();
  await waitFor(() => expect(api.current).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "Save paths" })).not.toBeInTheDocument();
});

it.each([
  ["SBS templates folder", "sbs_templates_root"], ["Orders root", "orders_root"],
  ["Customers and materials root", "materials_root"], ["Published materials library", "published_library_root"],
] as const)("populates %s from the native picker without saving", async (label, field) => {
  vi.spyOn(api, "current").mockResolvedValue(initial);
  const picker = vi.spyOn(api, "selectFolder").mockResolvedValue("C:\\Selected");
  const save = vi.spyOn(api, "save");
  mount(); fireEvent.click(await screen.findByRole("button", { name: `Choose folder for ${label}` }));
  await waitFor(() => expect(screen.getByLabelText(label)).toHaveValue("C:\\Selected"));
  expect(picker).toHaveBeenCalledExactlyOnceWith(field);
  expect(save).not.toHaveBeenCalled();
  expect(await screen.findByRole("status")).toHaveTextContent("Save paths to apply");
});

it("keeps the draft after cancellation or failure and permits manual input", async () => {
  vi.spyOn(api, "current").mockResolvedValue(initial);
  const picker = vi.spyOn(api, "selectFolder").mockResolvedValueOnce(null).mockRejectedValueOnce(new ApiError(503, "PRIVATE_FAILURE"));
  mount(); const input = await screen.findByLabelText("Orders root");
  fireEvent.change(input, { target: { value: "C:\\Manual" } });
  fireEvent.click(screen.getByRole("button", { name: "Choose folder for Orders root" }));
  await screen.findByText(/Folder selection cancelled/);
  expect(input).toHaveValue("C:\\Manual");
  fireEvent.click(screen.getByRole("button", { name: "Choose folder for Orders root" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/enter an existing absolute path manually/);
  expect(input).toHaveValue("C:\\Manual");
  fireEvent.change(input, { target: { value: "C:\\ManualAgain" } });
  expect(input).toHaveValue("C:\\ManualAgain");
  expect(screen.getByRole("button", { name: "Save paths" })).toBeEnabled();
  expect(picker).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: "Recover same paths request" })).not.toBeInTheDocument();
});

it("allows text fallback when a native picker is unavailable", async () => {
  vi.spyOn(api, "current").mockResolvedValue({ ...initial, canSelectFolder: false });
  const picker = vi.spyOn(api, "selectFolder");
  mount(); expect(await screen.findByLabelText("Published materials library")).toBeEnabled();
  const buttons = screen.getAllByRole("button", { name: /^Choose folder for/ });
  expect(buttons).toHaveLength(4);
  for (const button of buttons) { expect(button).toBeDisabled(); fireEvent.click(button); }
  expect(picker).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Save paths" })).toBeEnabled();
});

it("blocks duplicate pickers and saves until selection returns", async () => {
  vi.spyOn(api, "current").mockResolvedValue(initial);
  let resolve!: (value: string | null) => void;
  const picker = vi.spyOn(api, "selectFolder").mockReturnValue(new Promise(result => { resolve = result; }));
  const save = vi.spyOn(api, "save");
  mount(); const button = await screen.findByRole("button", { name: "Choose folder for Orders root" });
  fireEvent.click(button); fireEvent.click(button);
  fireEvent.click(screen.getByRole("button", { name: "Choose folder for Published materials library" }));
  fireEvent.click(screen.getByRole("button", { name: "Save paths" }));
  expect(picker).toHaveBeenCalledTimes(1);
  expect(save).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Orders root")).toBeDisabled();
  resolve(null);
  await waitFor(() => expect(screen.getByRole("button", { name: "Save paths" })).toBeEnabled());
});

it("ignores a picker reply received after the session changes", async () => {
  vi.spyOn(api, "current").mockResolvedValue(initial);
  let resolve!: (value: string | null) => void;
  vi.spyOn(api, "selectFolder").mockReturnValue(new Promise(result => { resolve = result; }));
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Choose folder for Orders root" }));
  setSessionToken(null);
  resolve("C:\\AnotherSessionsFolder");
  await waitFor(() => expect(screen.getByLabelText("Orders root")).toBeEnabled());
  expect(screen.getByLabelText("Orders root")).toHaveValue(initial.ordersRoot);
  expect(screen.queryByText(/Folder selected/)).not.toBeInTheDocument();
});
