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
const report = { items: [], report: "Checked: 1. OK: 1. Issues: 0.\nNo materials with issues.", reportPath: "C:\\Reports\\check.txt", reportOpened: true };
beforeEach(() => {
  vi.spyOn(materialLocalClient, "checkMany").mockResolvedValue(report);
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(() => { vi.restoreAllMocks(); setSessionToken(null); });

it("checks only explicit selection with versions and shows the report without local save details", async () => {
  const onChecked = vi.fn(), onBusyChange = vi.fn();
  render(<MaterialBulkCheck materials={[second]} onChecked={onChecked} onBusyChange={onBusyChange} />);
  expect(materialLocalClient.checkMany).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Auto-check materials (1)" }));
  expect(await screen.findByRole("textbox", { name: "Automatic file check report" })).toHaveValue(report.report);
  expect(screen.queryByText(report.reportPath)).not.toBeInTheDocument();
  expect(screen.queryByText(/Saved report:|opened in the desktop editor/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Download TXT report" })).toBeEnabled();
  expect(materialLocalClient.checkMany).toHaveBeenCalledExactlyOnceWith([{ id: second.id, updatedAt: second.updatedAt }], true, expect.objectContaining({ signal: expect.any(AbortSignal), onProgress: expect.any(Function), onPaused: expect.any(Function) }));
  expect(onChecked).toHaveBeenCalledOnce();
  expect(onBusyChange).toHaveBeenCalledWith(true); await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(false));
});

it("downloads the complete TXT report with the download action after the report", async () => {
  const create = vi.fn<(blob: Blob) => string>(() => "blob:check-report"), revoke = vi.fn();
  vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = revoke; });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  try {
    render(<MaterialBulkCheck materials={[first]} />);
    fireEvent.click(screen.getByRole("button", { name: "Auto-check materials (1)" }));
    const textbox = await screen.findByRole("textbox", { name: "Automatic file check report" });
    const download = screen.getByRole("button", { name: "Download TXT report" });
    expect(textbox.compareDocumentPosition(download) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    vi.useFakeTimers();
    fireEvent.click(download);
    expect(create).toHaveBeenCalledOnce();
    expect(create.mock.calls[0][0]).toMatchObject({ type: "text/plain;charset=utf-8", size: new TextEncoder().encode(report.report).length + 3 });
    expect(click).toHaveBeenCalledOnce();
    expect(click.mock.instances[0]).toMatchObject({ download: "reawote-material-check.txt", href: "blob:check-report" });
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith("blob:check-report");
  } finally { vi.useRealTimers(); vi.unstubAllGlobals(); }
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
  expect(screen.getByRole("status")).toHaveTextContent("Starting check of 2 materials");
  expect(requestNavigation("/projects")).toBe(false);
  await act(async () => finish(report));
  expect(requestNavigation("/projects")).toBe(true);
  expect(screen.queryByRole("region", { name: "File check progress" })).not.toBeInTheDocument();
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

it("renders live progress and resumes observation while keeping duplicate starts blocked", async () => {
  let finish!: (value: typeof report) => void;
  const resume = vi.fn(() => finish(report));
  vi.mocked(materialLocalClient.checkMany).mockImplementation((_items, _open, options) => new Promise(resolve => {
    finish = resolve;
    options?.onProgress?.({ id: "60000000-0000-4000-8000-000000000001", status: "RUNNING", total: 1, completed: 0,
      active: [{ materialId: first.id, identity: first.technicalIdentity, file: "PREVIEW/SPHERE_1.png", phase: "checking" }], elapsedSeconds: 8, cacheHits: 2, cacheMisses: 1 });
    options?.onPaused?.(resume);
  }));
  render(<MaterialBulkCheck materials={[first]} />);
  fireEvent.click(screen.getByRole("button", { name: "Auto-check materials (1)" }));
  expect(screen.getByText("PREVIEW/SPHERE_1.png")).toBeVisible();
  expect(screen.getByRole("button", { name: "Auto-checking materials…" })).toBeDisabled();
  expect(requestNavigation("/projects")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Resume file check" }));
  expect(await screen.findByRole("textbox")).toHaveValue(report.report);
  expect(resume).toHaveBeenCalledOnce(); expect(materialLocalClient.checkMany).toHaveBeenCalledOnce();
});

it("aborts observation on unmount without starting a replacement check", () => {
  let signal: AbortSignal | undefined;
  vi.mocked(materialLocalClient.checkMany).mockImplementation((_items, _open, options) => { signal = options?.signal; return new Promise(() => {}); });
  const { unmount } = render(<MaterialBulkCheck materials={[first]} />);
  fireEvent.click(screen.getByRole("button")); expect(signal?.aborted).toBe(false);
  unmount(); expect(signal?.aborted).toBe(true); expect(materialLocalClient.checkMany).toHaveBeenCalledOnce();
});

it("keeps a check and its report when the toolbar target disappears during refresh", async () => {
  let finish!: (value: typeof report) => void;
  vi.mocked(materialLocalClient.checkMany).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const toolbar = document.createElement("div");
  document.body.append(toolbar);
  try {
    const tree = render(<MaterialBulkCheck materials={[first]} compact actionTarget={toolbar} />);
    const start = screen.getByRole("button", { name: "Auto-check materials (1)" });
    expect(toolbar).toContainElement(start);
    fireEvent.click(start);
    tree.rerender(<MaterialBulkCheck materials={[]} compact actionTarget={null} />);
    expect(requestNavigation("/orders")).toBe(false);
    expect(screen.getByRole("button", { name: "Auto-checking materials…" })).toBeDisabled();
    await act(async () => finish(report));
    tree.rerender(<MaterialBulkCheck materials={[first]} compact actionTarget={toolbar} />);
    const view = screen.getByRole("button", { name: "View check report" });
    expect(toolbar).toContainElement(view);
    fireEvent.click(view);
    expect(screen.getByRole("textbox", { name: "Automatic file check report" })).toHaveValue(report.report);
    expect(screen.getByRole("button", { name: "Download TXT report" })).toBeEnabled();
    expect(materialLocalClient.checkMany).toHaveBeenCalledOnce();
    expect(requestNavigation("/orders")).toBe(true);
  } finally { toolbar.remove(); }
});

it("keeps an ongoing check and its report through compact progress dialog and page fallback", async () => {
  let finish!: (value: typeof report) => void;
  vi.mocked(materialLocalClient.checkMany).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const tree = render(<MaterialBulkCheck materials={[first]} compact />);
  fireEvent.click(screen.getByRole("button", { name: "Auto-check materials (1)" }));
  expect(screen.queryByRole("region", { name: "File check progress" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "View check progress" }));
  expect(screen.getByRole("dialog", { name: "Automatic file check details" })).toBeVisible();
  expect(screen.getByRole("region", { name: "File check progress" })).toBeVisible();
  tree.rerender(<MaterialBulkCheck materials={[first]} compact={false} />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "File check progress" })).toBeVisible();
  expect(materialLocalClient.checkMany).toHaveBeenCalledOnce();
  await act(async () => finish(report));
  expect(screen.getByRole("textbox", { name: "Automatic file check report" })).toHaveValue(report.report);
  tree.rerender(<MaterialBulkCheck materials={[first]} compact />);
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "View check report" }));
  expect(screen.getByRole("textbox", { name: "Automatic file check report" })).toHaveValue(report.report);
  expect(screen.getByRole("button", { name: "Download TXT report" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Close check details" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(materialLocalClient.checkMany).toHaveBeenCalledOnce();
});
