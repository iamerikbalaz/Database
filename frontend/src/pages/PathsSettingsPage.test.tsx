import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PathsSettingsPage } from "./PathsSettingsPage";
import { pathSettingsClient as api } from "../api/pathSettingsClient";
import { SessionContext } from "../auth/context";
import { processorDto } from "../test/materialFixtures";
import { ApiError } from "../api/errors";
import { requestNavigation } from "../navigationGuard";

const initial = { version: 0, sbsTemplatesRoot: "C:\\Templates", ordersRoot: "R:\\0. PROJECTS", materialsRoot: "C:\\Test_data" };
afterEach(() => vi.restoreAllMocks());
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
  expect(save.mock.calls[0][0]).toMatchObject({ expected_version: 0, sbs_templates_root: "C:\\NewTemplates" });
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
