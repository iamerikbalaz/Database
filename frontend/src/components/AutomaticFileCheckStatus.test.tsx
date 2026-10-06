import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AutomaticFileCheckStatus } from "./AutomaticFileCheckStatus";
import { storedFileCheckClient, storedFileCheckFromDto } from "../api/storedFileCheckClient";
import { materialLocalClient } from "../api/materialLocalClient";
import { materialFromDto } from "../api/materialDto";
import { materialDto, processorDto } from "../test/materialFixtures";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";

const material = { ...materialFromDto(materialDto), automaticFileCheckStatus: "ISSUES" as const, automaticFileCheckedAt: "2026-10-06T08:30:00Z" };
const wire = { material_id: material.id, current_status: "ISSUES", report: { status: "ISSUES", checked_at: material.automaticFileCheckedAt,
  profile: "PBR_FILES_V1", complete: true, is_current: true, issues: ["8K/COL.jpg: missing"], warnings: ["Legacy TIFF accepted"], text: "Stored source findings\n<img src=x>" } };
const saved = storedFileCheckFromDto(wire, material.id);
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); setSessionToken(null); });
it("fetches saved findings only on click and can reopen them after unmount", async () => {
  const get = vi.spyOn(storedFileCheckClient, "get").mockResolvedValue(saved);
  const check = vi.spyOn(materialLocalClient, "check"), bulk = vi.spyOn(materialLocalClient, "checkMany");
  const app = render(<AutomaticFileCheckStatus material={material} />);
  expect(get).not.toHaveBeenCalled();
  const button = screen.getByRole("button", { name: `Open automatic file check report for ${material.materialName}` });
  expect(button).toHaveTextContent("issues"); expect(button).toHaveClass("automatic-file-check--issues");
  fireEvent.click(button);
  const dialog = await screen.findByRole("dialog", { name: "Automatic file check" });
  const report = await within(dialog).findByRole("textbox", { name: "Automatic file check report" });
  expect((report as HTMLTextAreaElement).value).toContain("8K/COL.jpg: missing");
  expect((report as HTMLTextAreaElement).value).toContain("Legacy TIFF accepted");
  expect((report as HTMLTextAreaElement).value).toContain("<img src=x>");
  expect(document.querySelector("img[src=x]")).toBeNull();
  expect(within(dialog).getByRole("button", { name: "Download TXT report" })).toBeEnabled();
  expect(check).not.toHaveBeenCalled(); expect(bulk).not.toHaveBeenCalled();
  app.unmount();
  render(<AutomaticFileCheckStatus material={material} />);
  fireEvent.click(screen.getByRole("button", { name: `Open automatic file check report for ${material.materialName}` }));
  await screen.findByRole("textbox", { name: "Automatic file check report" });
  expect(get).toHaveBeenCalledTimes(2);
});
it("labels old results historical after the material's check was invalidated", async () => {
  vi.spyOn(storedFileCheckClient, "get").mockResolvedValue({ ...saved, currentStatus: "NOT_CHECKED", report: { ...saved.report!, isCurrent: false } });
  render(<AutomaticFileCheckStatus material={{ ...material, automaticFileCheckStatus: "NOT_CHECKED", automaticFileCheckedAt: null }} allowLastReport />);
  fireEvent.click(screen.getByRole("button", { name: "Last report" }));
  expect(await screen.findByText(/Historical report: the material changed/)).toHaveTextContent("current automatic check status is not checked");
  expect((screen.getByRole("textbox", { name: "Automatic file check report" }) as HTMLTextAreaElement).value).toContain("Historical report");
});
it("distinguishes missing reports from failed loading and allows a read-only retry", async () => {
  const get = vi.spyOn(storedFileCheckClient, "get").mockRejectedValueOnce(new Error("Offline")).mockResolvedValueOnce({ ...saved, report: null });
  render(<AutomaticFileCheckStatus material={material} />);
  fireEvent.click(screen.getByRole("button", { name: /Open automatic file check report/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Retry loading report" }));
  await screen.findByText("No saved automatic file check report is available for this material.");
  expect(get).toHaveBeenCalledTimes(2);
});
it("discards a late response from a previous account", async () => {
  let finish!: (value: typeof saved) => void;
  const get = vi.spyOn(storedFileCheckClient, "get").mockReturnValueOnce(new Promise(resolve => { finish = resolve; })).mockResolvedValueOnce({ ...saved, report: null });
  const tree = (id: string) => <SessionContext.Provider value={{ session: { user: { ...processorDto, id, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}><AutomaticFileCheckStatus material={material} /></SessionContext.Provider>;
  const app = render(tree(processorDto.id)); fireEvent.click(screen.getByRole("button", { name: /Open automatic file check report/ }));
  await waitFor(() => expect(get).toHaveBeenCalledOnce());
  setSessionToken("u".repeat(43)); app.rerender(tree("00000000-0000-4000-8000-000000000091"));
  await screen.findByText("No saved automatic file check report is available for this material.");
  await act(async () => finish(saved));
  expect(screen.queryByRole("textbox", { name: "Automatic file check report" })).not.toBeInTheDocument();
});
it("validates stored report identity, bounds and profile/timestamp", () => {
  expect(() => storedFileCheckFromDto({ ...wire, material_id: "00000000-0000-4000-8000-000000000099" }, material.id)).toThrow();
  expect(() => storedFileCheckFromDto({ ...wire, report: { ...wire.report, text: "x".repeat(1024 * 1024 + 1) } }, material.id)).toThrow();
  expect(() => storedFileCheckFromDto({ ...wire, report: { ...wire.report, checked_at: "yesterday" } }, material.id)).toThrow();
  expect(() => storedFileCheckFromDto({ ...wire, report: { ...wire.report, status: "PASS" } }, material.id)).toThrow();
});
it("uses GET without a worker or check command", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(wire))); vi.stubGlobal("fetch", fetch);
  expect(await storedFileCheckClient.get(material.id)).toEqual(saved);
  expect(fetch).toHaveBeenCalledWith(`/api/materials/${material.id}/automatic-file-check-report`, expect.objectContaining({ method: "GET", cache: "no-store", body: undefined }));
});
