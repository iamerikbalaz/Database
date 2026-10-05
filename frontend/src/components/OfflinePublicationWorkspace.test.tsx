import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mockApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import { materialFromDto } from "../api/materialDto";
import { materialLocalClient } from "../api/materialLocalClient";
import { localPublicationClient, type LocalPublicationJob, type LocalPublicationPreview } from "../api/localPublicationClient";
import { SessionContext } from "../auth/context";
import { requestNavigation } from "../navigationGuard";
import { materialDto, processorDto } from "../test/materialFixtures";
import { OfflinePublicationWorkspace } from "./OfflinePublicationWorkspace";

vi.mock("./PublicationMaterialsTable", () => ({ PublicationMaterialsTable: ({ onChanged, onBusyChange }: { onChanged: () => void; onBusyChange: (value: boolean) => void }) => <div aria-label="Batch editor"><button onClick={onChanged}>Save batch data</button><button onClick={() => onBusyChange(true)}>Begin batch editing</button></div> }));

const first = materialFromDto({ ...materialDto, material_name: "FIRST-MATERIAL" });
const second = { ...first, id: "50000000-0000-4000-8000-000000000002", materialName: "SECOND-MATERIAL" };
const selection = [first, second], ids = selection.map(item => item.id);
const preview: LocalPublicationPreview = { canPrepare: true, previewHash: "a".repeat(64), items: selection.map(item => ({
  materialId: item.id, name: item.materialName, identity: item.technicalIdentity, errors: [], warnings: [],
  row: { description: "Synthetic material", credits: 1, widthCm: "10", heightCm: "20", brandIdentifier: "synthetic", categories: ["Glass"], color: "#FFFFFF", tags: ["synthetic"] },
})) };
const job: LocalPublicationJob = { id: "60000000-0000-4000-8000-000000000001", materialIds: ids, status: "COMPLETED", outputPath: "C:\\Exports\\Selected-materials",
  csvName: "materials.csv", archives: [{ name: "synthetic.zip", sizeBytes: 1048576, sha256: "b".repeat(64) }], errorCode: null, published: false, issues: [] };
const report = { items: [], report: "Checked: 2. OK: 2. Issues: 0.\nNo materials with issues.", reportPath: "C:\\Reports\\check.txt", reportOpened: true };

beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value(this: HTMLDialogElement) { this.setAttribute("open", ""); } });
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value(this: HTMLDialogElement) { this.removeAttribute("open"); } });
  vi.spyOn(localPublicationClient, "preview").mockResolvedValue(preview);
  vi.spyOn(localPublicationClient, "destination").mockResolvedValue({ token: "opaque-destination", path: "C:\\Exports" });
  vi.spyOn(localPublicationClient, "create").mockResolvedValue(job);
  vi.spyOn(localPublicationClient, "detail").mockResolvedValue(job);
  vi.spyOn(localPublicationClient, "markPublished").mockResolvedValue(undefined);
  vi.spyOn(materialLocalClient, "checkMany").mockResolvedValue(report);
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

function setup() {
  const getMaterial = vi.fn(async (id: string) => ({ ...selection.find(item => item.id === id)!, updatedAt: "2026-09-28T15:00:00Z" }));
  const onChanged = vi.fn(), onBusyChange = vi.fn();
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <OfflinePublicationWorkspace client={{ ...mockApiClient, getMaterial }} navigate={vi.fn()} initialSelection={selection} onChanged={onChanged} onBusyChange={onBusyChange} />
  </SessionContext.Provider>);
  return { getMaterial, onChanged, onBusyChange };
}
async function review() {
  fireEvent.click(screen.getByRole("button", { name: "Review materials" }));
  await screen.findByRole("region", { name: "Publication review" });
}
async function prepare() {
  await review(); fireEvent.click(screen.getByRole("button", { name: "Prepare publication" }));
  await screen.findByRole("dialog", { name: "Mark materials as Published?" });
}

it("reviews only the explicit selection and shows publication warnings without automatic export", async () => {
  vi.mocked(localPublicationClient.preview).mockResolvedValue({ ...preview, items: preview.items.map(item => ({ ...item, warnings: [{ code: "CONTENT_DESCRIPTION_EMPTY", fields: [] }] })) });
  setup();
  expect(screen.getByRole("button", { name: "Prepare publication" })).toBeDisabled();
  expect(localPublicationClient.preview).not.toHaveBeenCalled();
  await review();
  expect(localPublicationClient.preview).toHaveBeenCalledExactlyOnceWith(ids);
  expect(screen.getAllByText("Description is empty.")).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Prepare publication" })).toBeEnabled();
  expect(materialLocalClient.checkMany).not.toHaveBeenCalled();
  expect(localPublicationClient.destination).not.toHaveBeenCalled(); expect(localPublicationClient.create).not.toHaveBeenCalled();
  expect(localPublicationClient.markPublished).not.toHaveBeenCalled();
});

it("runs the optional check on refreshed selected records before reviewing publication data", async () => {
  const { getMaterial, onChanged } = setup();
  fireEvent.click(screen.getByRole("checkbox", { name: "Run automatic file check" }));
  expect(screen.getByText(/Review includes the full map, metadata and preview checks/)).toBeVisible();
  await review();
  expect(getMaterial.mock.calls.map(args => args[0])).toEqual(ids);
  expect(materialLocalClient.checkMany).toHaveBeenCalledExactlyOnceWith(selection.map(item => ({ ...item, updatedAt: "2026-09-28T15:00:00Z" })), true, expect.objectContaining({ signal: expect.any(AbortSignal), onProgress: expect.any(Function), onPaused: expect.any(Function) }));
  expect(vi.mocked(materialLocalClient.checkMany).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(localPublicationClient.preview).mock.invocationCallOrder[0]!);
  expect(screen.getByRole("textbox", { name: "Automatic file check report" })).toHaveValue(report.report);
  expect(onChanged).toHaveBeenCalledOnce();
});

it("does not review or prepare after an optional file check fails", async () => {
  vi.mocked(materialLocalClient.checkMany).mockRejectedValue(new ApiError(409, "Source changed"));
  setup(); fireEvent.click(screen.getByRole("checkbox", { name: "Run automatic file check" }));
  fireEvent.click(screen.getByRole("button", { name: "Review materials" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("could not be reviewed");
  expect(localPublicationClient.preview).not.toHaveBeenCalled(); expect(localPublicationClient.create).not.toHaveBeenCalled();
});

it("checks only records without an automatic result by default, using their current state", async () => {
  const { getMaterial } = setup();
  getMaterial.mockImplementation(async id => ({ ...selection.find(item => item.id === id)!, automaticFileCheckStatus: id === first.id ? "NOT_CHECKED" : "ISSUES", updatedAt: "2026-10-05T12:00:00Z" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Run automatic file check" }));
  expect(screen.getByRole("combobox", { name: "Check scope" })).toHaveValue("unchecked");
  await review();
  expect(vi.mocked(materialLocalClient.checkMany).mock.calls[0][0].map(item => item.id)).toEqual([first.id]);
  expect(localPublicationClient.preview).toHaveBeenCalledWith(ids);
});

it("can explicitly recheck every material in the publication batch", async () => {
  const { getMaterial } = setup();
  getMaterial.mockImplementation(async id => ({ ...selection.find(item => item.id === id)!, automaticFileCheckStatus: "OK", updatedAt: "2026-10-05T12:00:00Z" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Run automatic file check" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Check scope" }), { target: { value: "all" } });
  await review();
  expect(vi.mocked(materialLocalClient.checkMany).mock.calls[0][0].map(item => item.id)).toEqual(ids);
});

it("reviews without starting an empty check job when all records have results", async () => {
  const { getMaterial } = setup();
  getMaterial.mockImplementation(async id => ({ ...selection.find(item => item.id === id)!, automaticFileCheckStatus: "OK", updatedAt: "2026-10-05T12:00:00Z" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Run automatic file check" }));
  await review();
  expect(materialLocalClient.checkMany).not.toHaveBeenCalled();
  expect(screen.getByRole("status")).toHaveTextContent("No checks were repeated");
});

it("invalidates an export review after library data changes and freezes export while editing", async () => {
  setup(); await review();
  fireEvent.click(screen.getByRole("button", { name: "Save batch data" }));
  expect(screen.queryByRole("region", { name: "Publication review" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Prepare publication" })).toBeDisabled();
  await review();
  fireEvent.click(screen.getByRole("button", { name: "Begin batch editing" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Prepare publication" })).toBeDisabled());
  expect(screen.getByRole("button", { name: "Review materials" })).toBeDisabled();
});

it("keeps file-check recovery enabled outside the frozen publication controls", async () => {
  let finish!: (value: typeof report) => void;
  const resume = vi.fn(() => finish(report));
  vi.mocked(materialLocalClient.checkMany).mockImplementation((_items, _open, options) => new Promise(resolve => {
    finish = resolve; options?.onPaused?.(resume);
  }));
  setup(); fireEvent.click(screen.getByRole("checkbox", { name: "Run automatic file check" }));
  fireEvent.click(screen.getByRole("button", { name: "Review materials" }));
  const recover = await screen.findByRole("button", { name: "Resume file check" });
  expect(recover).toBeEnabled(); expect(screen.getByRole("checkbox", { name: "Run automatic file check" })).toBeDisabled();
  expect(localPublicationClient.preview).not.toHaveBeenCalled(); expect(requestNavigation("/projects")).toBe(false);
  fireEvent.click(recover); await screen.findByRole("region", { name: "Publication review" });
  expect(resume).toHaveBeenCalledOnce(); expect(materialLocalClient.checkMany).toHaveBeenCalledOnce();
});

it("blocks preparation while required data is missing", async () => {
  vi.mocked(localPublicationClient.preview).mockResolvedValue({ ...preview, canPrepare: false, items: preview.items.map(item => ({ ...item, errors: ["EXPORT_COLOR_INVALID"] })) });
  setup(); await review();
  expect(screen.getAllByText("Choose a valid color.")).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Prepare publication" })).toBeDisabled();
  expect(localPublicationClient.destination).not.toHaveBeenCalled();
});

it("picker cancellation exports nothing and permits choosing a destination again", async () => {
  vi.mocked(localPublicationClient.destination).mockResolvedValueOnce(null);
  setup(); await review(); fireEvent.click(screen.getByRole("button", { name: "Prepare publication" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Folder selection canceled. Nothing was exported.");
  expect(localPublicationClient.create).not.toHaveBeenCalled(); expect(localPublicationClient.markPublished).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Prepare publication" })).toBeEnabled(); expect(requestNavigation("/materials")).toBe(true);
});

it("shows completed CSV and ZIP receipt and asks before changing Published", async () => {
  setup(); await prepare();
  expect(localPublicationClient.create).toHaveBeenCalledWith(expect.objectContaining({ material_ids: ids, expected_preview_hash: preview.previewHash, destination_token: "opaque-destination", idempotency_key: expect.any(String) }));
  expect(screen.getByRole("region", { name: "Prepared publication files" })).toHaveTextContent("materials.csv · 1 ZIP archives");
  expect(screen.getByText(job.outputPath!)).toBeVisible();
  expect(localPublicationClient.markPublished).not.toHaveBeenCalled();
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Not now" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByText("Published: Unchanged")).toBeVisible(); expect(localPublicationClient.markPublished).not.toHaveBeenCalled();
});

it("marks the complete exported selection in one explicit confirmation", async () => {
  const { onChanged } = setup(); await prepare();
  const button = within(screen.getByRole("dialog")).getByRole("button", { name: "Yes, mark Published" });
  act(() => { button.click(); button.click(); });
  await screen.findByText("Published: Yes");
  expect(localPublicationClient.markPublished).toHaveBeenCalledExactlyOnceWith(job.id, ids, expect.any(String));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(onChanged).toHaveBeenCalledOnce();
});

it("recovers an uncertain export with the same destination and idempotency key", async () => {
  vi.mocked(localPublicationClient.create).mockRejectedValueOnce(new TypeError("Connection lost"));
  setup(); await review(); fireEvent.click(screen.getByRole("button", { name: "Prepare publication" }));
  await screen.findByRole("button", { name: "Recover preparation" });
  expect(requestNavigation("/materials")).toBe(false);
  const request = vi.mocked(localPublicationClient.create).mock.calls[0]![0];
  fireEvent.click(screen.getByRole("button", { name: "Recover preparation" }));
  await screen.findByRole("dialog");
  expect(localPublicationClient.destination).toHaveBeenCalledOnce();
  expect(localPublicationClient.create).toHaveBeenCalledTimes(2);
  expect(vi.mocked(localPublicationClient.create).mock.calls[1]![0]).toEqual(request);
  expect(localPublicationClient.markPublished).not.toHaveBeenCalled();
});

it("recovers an uncertain Published confirmation with the same key and keeps the dialog open", async () => {
  vi.mocked(localPublicationClient.markPublished).mockRejectedValueOnce(new TypeError("Connection lost"));
  setup(); await prepare();
  fireEvent.click(screen.getByRole("button", { name: "Yes, mark Published" }));
  await screen.findByRole("button", { name: "Retry same confirmation" });
  expect(screen.getByRole("button", { name: "Not now" })).toBeDisabled();
  expect(requestNavigation("/materials")).toBe(false);
  const original = vi.mocked(localPublicationClient.markPublished).mock.calls[0];
  fireEvent.click(screen.getByRole("button", { name: "Retry same confirmation" }));
  await screen.findByText("Published: Yes");
  expect(vi.mocked(localPublicationClient.markPublished).mock.calls[1]).toEqual(original);
});

it("keeps prepared files and Published unchanged after a definitive confirmation rejection", async () => {
  vi.mocked(localPublicationClient.markPublished).mockRejectedValue(new ApiError(409, "A selected material changed"));
  const { onChanged } = setup(); await prepare();
  fireEvent.click(screen.getByRole("button", { name: "Yes, mark Published" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Material data or permissions may have changed");
  expect(screen.getByRole("region", { name: "Prepared publication files" })).toHaveTextContent("Published: Unchanged");
  expect(screen.getByRole("button", { name: "Not now" })).toBeEnabled(); expect(onChanged).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByText(job.outputPath!)).toBeVisible();
});

it("requires a fresh review after stale material or destination rejection", async () => {
  vi.mocked(localPublicationClient.create).mockRejectedValue(new ApiError(409, "Stale preview"));
  setup(); await review(); fireEvent.click(screen.getByRole("button", { name: "Prepare publication" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Review the materials again");
  expect(screen.queryByRole("region", { name: "Publication review" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Prepare publication" })).toBeDisabled();
  expect(requestNavigation("/materials")).toBe(true);
});

it("shows per-material file issues after packaging fails and never offers Published", async () => {
  vi.mocked(localPublicationClient.create).mockResolvedValue({ ...job, status:"FAILED", outputPath:null, archives:[],
    errorCode:"LOCAL_EXPORT_MATERIAL_FILES_INVALID", issues:[{ materialId:first.id, issues:[{ code:"MAP_FILENAME_INVALID", path:"4K/invalid.png" }] }] });
  setup(); await review(); fireEvent.click(screen.getByRole("button", { name:"Prepare publication" }));
  const issues = await screen.findByRole("region", { name:"Export file issues" });
  expect(issues).toHaveTextContent("FIRST-MATERIAL"); expect(issues).toHaveTextContent("4K/invalid.png");
  expect(issues).toHaveTextContent("map filename invalid");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(localPublicationClient.markPublished).not.toHaveBeenCalled();
});

it("polls the same running job and opens Published confirmation only after completion", async () => {
  vi.mocked(localPublicationClient.create).mockResolvedValue({ ...job, status: "RUNNING", outputPath: null, csvName: null, archives: [] });
  setup(); await review(); vi.useFakeTimers();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Prepare publication" })); });
  expect(screen.getByRole("status")).toHaveTextContent("Preparing CSV and ZIP files");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(requestNavigation("/materials")).toBe(false);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(localPublicationClient.detail).toHaveBeenCalledExactlyOnceWith(job.id, ids, expect.any(AbortSignal));
  expect(screen.getByRole("dialog", { name: "Mark materials as Published?" })).toBeVisible();
  expect(localPublicationClient.create).toHaveBeenCalledOnce(); expect(localPublicationClient.markPublished).not.toHaveBeenCalled();
});

it("resumes polling a running export after a transient read failure without creating another", async () => {
  vi.mocked(localPublicationClient.create).mockResolvedValue({ ...job, status: "RUNNING", outputPath: null, csvName: null, archives: [] });
  vi.mocked(localPublicationClient.detail).mockRejectedValueOnce(new TypeError("Network unavailable"));
  setup(); await review(); vi.useFakeTimers();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Prepare publication" })); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByRole("alert")).toHaveTextContent("second export is not needed");
  fireEvent.click(screen.getByRole("button", { name: "Resume checking export" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByRole("dialog")).toBeVisible(); expect(localPublicationClient.create).toHaveBeenCalledOnce();
  expect(localPublicationClient.detail).toHaveBeenCalledTimes(2);
});
