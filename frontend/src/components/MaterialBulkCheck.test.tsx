import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { materialFromDto } from "../api/materialDto";
import { materialLocalClient } from "../api/materialLocalClient";
import { setSessionToken } from "../auth/sessionTransport";
import { requestNavigation } from "../navigationGuard";
import { materialDto } from "../test/materialFixtures";
import { MaterialBulkCheck } from "./MaterialBulkCheck";

const first = materialFromDto(materialDto);
const second = { ...first, id: "50000000-0000-4000-8000-000000000002", materialName: "SECOND-MATERIAL" };
const report = { items: [], report: "Preliminary check of selected materials. Final rules are not configured.", reportPath: "C:\\Reports\\check.txt", reportOpened: true };
beforeEach(() => { vi.spyOn(materialLocalClient, "checkMany").mockResolvedValue(report); });
afterEach(() => { vi.restoreAllMocks(); setSessionToken(null); });

it("checks only explicit selection with versions and displays its saved TXT report", async () => {
  const onChecked = vi.fn(), onBusyChange = vi.fn();
  render(<MaterialBulkCheck materials={[second]} onChecked={onChecked} onBusyChange={onBusyChange} />);
  expect(materialLocalClient.checkMany).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Check selected materials (1)" }));
  expect(await screen.findByRole("textbox", { name: "Automatic file check report" })).toHaveValue(report.report);
  expect(screen.getByText(report.reportPath)).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent("opened in the desktop editor");
  expect(screen.getByRole("button", { name: "Download TXT report" })).toBeEnabled();
  expect(materialLocalClient.checkMany).toHaveBeenCalledExactlyOnceWith([{ id: second.id, updatedAt: second.updatedAt }]);
  expect(onChecked).toHaveBeenCalledOnce();
  expect(onBusyChange).toHaveBeenCalledWith(true); await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(false));
});

it.each([{ materials: [] }, { materials: Array.from({ length: 101 }, () => first) }, { materials: [first], disabled: true }])("does not send empty, oversized or disabled checks", ({ materials, disabled }) => {
  render(<MaterialBulkCheck materials={materials} disabled={disabled ?? false} />);
  const button = screen.getByRole("button"); expect(button).toBeDisabled(); fireEvent.click(button);
  expect(materialLocalClient.checkMany).not.toHaveBeenCalled();
});

it("keeps one pending selection snapshot and blocks duplicate submissions and navigation", async () => {
  let finish!: (value: typeof report) => void;
  vi.mocked(materialLocalClient.checkMany).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<MaterialBulkCheck materials={[first, second]} />);
  const button = screen.getByRole("button");
  act(() => { button.click(); button.click(); });
  expect(materialLocalClient.checkMany).toHaveBeenCalledOnce(); expect(button).toBeDisabled();
  expect(requestNavigation("/projects")).toBe(false);
  await act(async () => finish(report));
  expect(requestNavigation("/projects")).toBe(true);
});

it("does not show stale private reports after the authenticated session changes", async () => {
  let finish!: (value: typeof report) => void;
  const onChecked = vi.fn();
  vi.mocked(materialLocalClient.checkMany).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<MaterialBulkCheck materials={[first]} onChecked={onChecked} />);
  fireEvent.click(screen.getByRole("button")); setSessionToken(null);
  await act(async () => finish(report));
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument(); expect(onChecked).not.toHaveBeenCalled();
});

it("shows retry guidance without claiming success when the check fails", async () => {
  vi.mocked(materialLocalClient.checkMany).mockRejectedValue(new Error("private IO failure"));
  const onChecked = vi.fn(); render(<MaterialBulkCheck materials={[first]} onChecked={onChecked} />);
  fireEvent.click(screen.getByRole("button"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Refresh materials");
  expect(screen.queryByText(/private IO failure/)).not.toBeInTheDocument();
  expect(onChecked).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole("button")).toBeEnabled());
});

it("keeps the report readable and downloadable if the desktop editor did not open", async () => {
  vi.mocked(materialLocalClient.checkMany).mockResolvedValue({ ...report, reportPath: null, reportOpened: false });
  render(<MaterialBulkCheck materials={[first]} />); fireEvent.click(screen.getByRole("button"));
  expect(await screen.findByRole("textbox")).toHaveValue(report.report);
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Download TXT report" })).toBeEnabled();
});
