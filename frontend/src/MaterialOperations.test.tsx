import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { mockApiClient, type ApiClient } from "./api/client";
import { ApiError } from "./api/errors";
import { materialFromDto, type Material } from "./api/materialDto";
import {
  materialFolderPreflightFromDto,
  materialMetadataFromDto,
  materialMetadataSnapshotFromDto,
  type MaterialFolderPreflight,
  type MaterialMetadata,
  type MaterialMetadataSnapshot,
} from "./api/materialOperationsDto";
import {
  materialBrand,
  materialDto,
  materialProject,
  metadataDto,
  preflightDto,
  processorDto,
  snapshotDto,
} from "./test/materialFixtures";

Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
  configurable: true,
  writable: true,
  value() {},
});
Object.defineProperty(HTMLDialogElement.prototype, "close", {
  configurable: true,
  writable: true,
  value() {},
});

beforeEach(() => {
  vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
    this.querySelector<HTMLButtonElement>("button")?.focus();
  });
  vi.spyOn(HTMLDialogElement.prototype, "close").mockImplementation(function (this: HTMLDialogElement) {
    this.removeAttribute("open");
    this.dispatchEvent(new Event("close"));
  });
});
afterEach(() => vi.restoreAllMocks());

interface ClientOptions {
  material?: Material;
  linked?: boolean;
  done?: boolean;
  preflight?: MaterialFolderPreflight;
  metadata?: MaterialMetadata;
  snapshots?: MaterialMetadataSnapshot[];
  snapshotError?: unknown;
  snapshotPromise?: Promise<never>;
  preflightError?: unknown;
  preflightPromise?: Promise<never>;
  linkError?: unknown;
  doneError?: unknown;
  linkPromise?: Promise<never>;
  donePromise?: Promise<never>;
}

function operationClient(options: ClientOptions = {}) {
  let material: Material = options.material ?? materialFromDto({
    ...materialDto,
    folder_path: options.linked || options.done ? `library/${materialDto.technical_identity}` : null,
    workflow_status: options.done ? "DONE" : "IN_PROGRESS",
  });
  let metadata = options.metadata ?? materialMetadataFromDto(metadataDto);
  let snapshots = options.snapshots ?? [];
  const preflight = options.preflight ?? materialFolderPreflightFromDto(preflightDto);
  const getMaterial = vi.fn(async () => material);
  const getMaterialMetadata = vi.fn(async () => metadata);
  const getMaterialMetadataSnapshots = vi.fn(async () => {
    if (options.snapshotPromise) return options.snapshotPromise;
    if (options.snapshotError) throw options.snapshotError;
    return snapshots;
  });
  const preflightMaterialFolder = vi.fn(async () => {
    if (options.preflightPromise) return options.preflightPromise;
    if (options.preflightError) throw options.preflightError;
    return preflight;
  });
  const linkMaterialFolder = vi.fn(async (_id: string, folderPath: string) => {
    if (options.linkPromise) return options.linkPromise;
    if (options.linkError) throw options.linkError;
    material = { ...material, folderPath };
    return { material, preflight };
  });
  const markMaterialDone = vi.fn(async () => {
    if (options.donePromise) return options.donePromise;
    if (options.doneError) throw options.doneError;
    material = { ...material, workflowStatus: "DONE" as const };
    metadata = options.metadata ?? materialMetadataFromDto({
      ...metadataDto,
      current_snapshot_id: snapshotDto.id,
      status: "VALID",
      source_filename: "metadata.txt",
      source_sha256: "a".repeat(64),
      hex_color: "#A1B2C3",
      width_cm: "120.5000",
      height_cm: "75.2500",
      master_resolution: "16K",
      loaded_at: snapshotDto.loaded_at,
    });
    const snapshot = options.snapshots?.[0] ?? materialMetadataSnapshotFromDto(snapshotDto);
    snapshots = options.snapshots ?? [snapshot];
    return { material, metadata, snapshot, preflight };
  });
  const client: ApiClient = {
    ...mockApiClient,
    getMaterial,
    getProjectRecord: vi.fn(async () => ({
      id: materialProject.id, companyId: materialProject.company_id,
      number: materialProject.project_number, name: materialProject.name,
      status: "in_progress" as const, dueDate: materialProject.due_date,
      description: materialProject.notes, createdAt: materialProject.created_at,
      updatedAt: materialProject.updated_at,
    })),
    getBrand: vi.fn(async () => ({
      id: materialBrand.id, companyId: materialBrand.company_id, name: materialBrand.name,
      folderPrefix: materialBrand.folder_prefix, brandIdentifier: materialBrand.brand_identifier,
      nextSequenceNumber: materialBrand.next_sequence_number, isActive: materialBrand.is_active,
      createdAt: materialBrand.created_at, updatedAt: materialBrand.updated_at,
    })),
    getInternalUser: vi.fn(async () => ({
      id: processorDto.id, displayName: processorDto.display_name, email: processorDto.email,
      role: processorDto.role, isActive: true, createdAt: processorDto.created_at,
      updatedAt: processorDto.updated_at,
    })),
    getMaterialMetadata,
    getMaterialMetadataSnapshots,
    preflightMaterialFolder,
    linkMaterialFolder,
    markMaterialDone,
  };
  return {
    client,
    getMaterial,
    getMaterialMetadata,
    getMaterialMetadataSnapshots,
    preflightMaterialFolder,
    linkMaterialFolder,
    markMaterialDone,
  };
}

async function renderDetail(options: ClientOptions = {}) {
  const result = operationClient(options);
  render(<App client={result.client} initialPath={`/materials/${materialDto.id}`} />);
  await screen.findByRole("heading", { name: materialDto.material_name });
  await screen.findByRole("heading", { name: "Current metadata" });
  return result;
}

async function runPreflight(expectResult = true) {
  fireEvent.change(screen.getByLabelText("Relative folder path"), {
    target: { value: `library/${materialDto.technical_identity}` },
  });
  fireEvent.click(screen.getByRole("button", { name: "Check folder" }));
  if (expectResult) return screen.findByLabelText("Folder check result");
  await waitFor(() => expect(screen.getByRole("button", { name: "Check folder" })).toBeEnabled());
  return null;
}

async function openAndConfirm(buttonName: string, dialogName: string) {
  fireEvent.click(screen.getByRole("button", { name: buttonName }));
  const dialog = screen.getByRole("dialog", { name: dialogName });
  fireEvent.click(within(dialog).getByRole("button", { name: buttonName }));
}

describe("material folder and Done UI", () => {
  it("shows an unlinked material, current NOT_SCANNED metadata and an empty history", async () => {
    await renderDetail();
    expect(screen.getByText("Folder status").nextElementSibling).toHaveTextContent("Not linked");
    expect(screen.queryByRole("button", { name: "Mark as Done" })).not.toBeInTheDocument();
    expect(await screen.findByText("not scanned")).toBeInTheDocument();
    expect(await screen.findByText("No metadata snapshots yet.")).toBeInTheDocument();
  });

  it("never displays or acts on an absolute server folder path", async () => {
    await renderDetail({ material: { ...materialFromDto(materialDto), folderPath: "C:\\private\\materials\\secret" } });
    expect(screen.getByText("Folder path").nextElementSibling).toHaveTextContent("unsafe path hidden");
    expect(screen.queryByText(/C:\\private/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark as Done" })).not.toBeInTheDocument();
  });

  it("shows a successful preflight with normalized technical data", async () => {
    const { preflightMaterialFolder } = await renderDetail();
    const result = await runPreflight();
    for (const text of [materialDto.technical_identity, "Yes", "valid", "metadata.txt", "#A1B2C3", "120.5000 × 75.2500 cm", "16K"])
      expect(within(result!).getAllByText(text).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Link folder" })).toBeEnabled();
    expect(preflightMaterialFolder).toHaveBeenCalledWith(materialDto.id, `library/${materialDto.technical_identity}`);
  });

  it("shows preflight loading and prevents a double submission", async () => {
    let reject!: (reason: unknown) => void;
    const pending = new Promise<never>((_resolve, rejectPromise) => { reject = rejectPromise; });
    const { preflightMaterialFolder } = await renderDetail({ preflightPromise: pending });
    const input = screen.getByLabelText("Relative folder path");
    fireEvent.change(input, { target: { value: `library/${materialDto.technical_identity}` } });
    const form = screen.getByRole("form", { name: "Check material folder" });
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(preflightMaterialFolder).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();
    expect(input).toBeDisabled();
    await act(async () => reject(new TypeError("Network unavailable")));
  });

  it("blocks linking when identity does not match", async () => {
    const mismatch = materialFolderPreflightFromDto({
      ...preflightDto,
      folder_name: "OTHER_0001_G03",
      identity_matches: false,
      can_continue: false,
      errors: [{
        code: "TECHNICAL_IDENTITY_MISMATCH",
        message: "Folder name does not match.",
        expected_technical_identity: materialDto.technical_identity,
        actual_folder_name: "OTHER_0001_G03",
      }],
    });
    await renderDetail({ preflight: mismatch });
    await runPreflight();
    expect(screen.getByRole("button", { name: "Link folder" })).toBeDisabled();
    expect(screen.getAllByText(/OTHER_0001_G03/).length).toBeGreaterThan(0);
  });

  it("blocks linking when can_continue is false", async () => {
    await renderDetail({ preflight: materialFolderPreflightFromDto({
      ...preflightDto,
      can_continue: false,
      errors: [{ code: "MATERIAL_MISSING", message: "Folder is unavailable.", path: null }],
    }) });
    await runPreflight();
    expect(screen.getByRole("button", { name: "Link folder" })).toBeDisabled();
    expect(screen.getByText(/Folder is unavailable/)).toBeInTheDocument();
  });

  it("allows linking when metadata has a warning and can_continue is true", async () => {
    await renderDetail({ preflight: materialFolderPreflightFromDto({
      ...preflightDto,
      metadata_status: "WARNING",
      warnings: [{ code: "HEX_COLOR_MISSING", message: "Color is missing.", path: "metadata.txt" }],
    }) });
    await runPreflight();
    expect(screen.getByText(/Color is missing/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Link folder" })).toBeEnabled();
  });

  it("confirms and links a folder, then reloads material detail", async () => {
    const { linkMaterialFolder, getMaterial } = await renderDetail();
    await runPreflight();
    await openAndConfirm("Link folder", "Link this folder?");
    expect(await screen.findByText(/was linked successfully/)).toBeInTheDocument();
    expect(linkMaterialFolder).toHaveBeenCalledTimes(1);
    expect(getMaterial).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Folder status").nextElementSibling).toHaveTextContent("Linked");
  });

  it("shows and focuses a link conflict", async () => {
    await renderDetail({ linkError: new ApiError(409, "Conflict") });
    await runPreflight();
    await openAndConfirm("Link folder", "Link this folder?");
    const error = await screen.findByRole("alert", { name: "" });
    expect(error).toHaveTextContent("already used or the material changed");
    expect(error).toHaveFocus();
  });

  it.each([
    ["404", new ApiError(404, "Missing"), /no longer exists/],
    ["422", new ApiError(422, "Unsafe"), /did not pass the required safety checks/],
    ["503", new ApiError(503, "Unavailable"), /temporarily unavailable/],
    ["network", new TypeError("Network unavailable"), /did not reach the server/],
  ])("handles a folder-link %s failure and focuses the error", async (_label, failure, message) => {
    await renderDetail({ linkError: failure });
    await runPreflight();
    await openAndConfirm("Link folder", "Link this folder?");
    const errorText = await screen.findByText(message);
    expect(errorText.closest("[role=alert]")).toHaveFocus();
  });

  it.each([
    [404, "no longer exists"],
    [422, "invalid or the folder could not be checked safely"],
    [503, "temporarily unavailable"],
  ])("handles preflight HTTP %s and focuses the error", async (status, message) => {
    await renderDetail({ preflightError: new ApiError(status, "Failed") });
    await runPreflight(false);
    const error = await screen.findByText(new RegExp(message));
    expect(error.closest("[role=alert]")).toHaveFocus();
  });

  it("handles a preflight network failure", async () => {
    await renderDetail({ preflightError: new TypeError("Network unavailable") });
    await runPreflight(false);
    expect(await screen.findByText(/did not reach the server/)).toBeInTheDocument();
  });

  it.each(["VALID", "MISSING"] as const)("marks Done with %s metadata and refreshes all related data", async (status) => {
    const warning = status === "MISSING" ? [{ code: "SOURCE_METADATA_MISSING", message: "Metadata file is missing.", path: null }] : [];
    const current = materialMetadataFromDto({ ...metadataDto, status, warnings: warning });
    const { markMaterialDone, getMaterialMetadata, getMaterialMetadataSnapshots } = await renderDetail({ linked: true, metadata: current });
    fireEvent.click(screen.getByRole("button", { name: "Mark as Done" }));
    const dialog = screen.getByRole("dialog", { name: "Mark this material as Done?" });
    expect(dialog).toHaveTextContent("does not by itself prevent");
    fireEvent.click(within(dialog).getByRole("button", { name: "Mark as Done" }));
    expect(await screen.findByText(/Material was marked as Done/)).toBeInTheDocument();
    expect(markMaterialDone).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(getMaterialMetadata).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getMaterialMetadataSnapshots).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("button", { name: "Mark as Done" })).not.toBeInTheDocument();
    if (status === "MISSING") expect(screen.getByText(/Metadata file is missing/)).toBeInTheDocument();
  });

  it("shows and focuses a repeated Done conflict", async () => {
    await renderDetail({ linked: true, doneError: new ApiError(409, "Already done") });
    await openAndConfirm("Mark as Done", "Mark this material as Done?");
    const message = await screen.findByText(/already Done or changed/);
    expect(message.closest("[role=alert]")).toHaveFocus();
  });

  it("prevents a double Done submission while the first request is pending", async () => {
    let reject!: (reason: unknown) => void;
    const pending = new Promise<never>((_resolve, rejectPromise) => { reject = rejectPromise; });
    const { markMaterialDone } = await renderDetail({ linked: true, donePromise: pending });
    fireEvent.click(screen.getByRole("button", { name: "Mark as Done" }));
    const dialog = screen.getByRole("dialog", { name: "Mark this material as Done?" });
    const confirm = within(dialog).getByRole("button", { name: "Mark as Done" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(markMaterialDone).toHaveBeenCalledTimes(1);
    expect(within(dialog).getByRole("button", { name: "Marking as Done…" })).toBeDisabled();
    await act(async () => reject(new ApiError(503, "Unavailable")));
  });

  it("closes confirmation with Escape and restores focus", async () => {
    await renderDetail({ linked: true });
    const trigger = screen.getByRole("button", { name: "Mark as Done" });
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Mark this material as Done?" });
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(screen.queryByRole("dialog", { name: "Mark this material as Done?" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});

describe("material metadata UI", () => {
  it("shows current safe metadata without raw source fields", async () => {
    const metadata = materialMetadataFromDto({
      ...metadataDto,
      status: "WARNING",
      source_filename: "metadata.txt",
      source_sha256: "b".repeat(64),
      hex_color: "#D4E5F6",
      width_cm: "121.7500",
      height_cm: "75.2500",
      master_resolution: "16K",
      loaded_at: "2026-09-11T11:30:00Z",
      warnings: [{ code: "REVIEW", message: "Review dimensions.", path: "metadata.txt" }],
      raw_content: "RAW MUST STAY HIDDEN",
      source_content: "SOURCE MUST STAY HIDDEN",
    } as typeof metadataDto);
    await renderDetail({ metadata });
    for (const text of ["warning", "metadata.txt", "#D4E5F6", "121.7500 cm", "75.2500 cm", "16K", "2026-09-11T11:30:00Z"])
      expect((await screen.findAllByText(text)).length).toBeGreaterThan(0);
    expect(screen.getByText(/Review dimensions/)).toBeInTheDocument();
    expect(screen.queryByText(/RAW MUST STAY HIDDEN|SOURCE MUST STAY HIDDEN/)).not.toBeInTheDocument();
    expect(screen.queryByText(/raw_content|source_content/i)).not.toBeInTheDocument();
  });

  it("shows multiple snapshots in stable sequence order", async () => {
    const first = materialMetadataSnapshotFromDto(snapshotDto);
    const second = materialMetadataSnapshotFromDto({
      ...snapshotDto,
      id: "60000000-0000-4000-8000-000000000002",
      sequence_number: 2,
      status: "WARNING",
      loaded_at: "2026-09-11T12:30:00Z",
      created_at: "2026-09-11T12:30:01Z",
    });
    await renderDetail({ snapshots: [second, first] });
    const items = await screen.findAllByText(/Snapshot [12]/);
    expect(items.map((item) => item.textContent)).toEqual(["Snapshot 1", "Snapshot 2"]);
    expect(screen.getAllByText("2026-09-11T10:30:00Z").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2026-09-11T12:30:00Z").length).toBeGreaterThan(0);
  });

  it("shows snapshot loading and a focused error state", async () => {
    let reject!: (reason: unknown) => void;
    const pending = new Promise<never>((_resolve, rejectPromise) => { reject = rejectPromise; });
    await renderDetail({ snapshotPromise: pending });
    expect(screen.getByText("Loading snapshot history…")).toBeInTheDocument();
    await act(async () => reject(new TypeError("Network unavailable")));
    const error = await screen.findByText(/metadata service could not be reached/);
    expect(error.closest("[role=alert]")).toHaveFocus();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
