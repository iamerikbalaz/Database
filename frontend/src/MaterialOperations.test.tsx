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
  initialMetadataPromise?: Promise<MaterialMetadata>;
  snapshots?: MaterialMetadataSnapshot[];
  initialSnapshotsPromise?: Promise<MaterialMetadataSnapshot[]>;
  snapshotError?: unknown;
  snapshotPromise?: Promise<never>;
  preflightError?: unknown;
  preflightPromise?: Promise<never>;
  linkError?: unknown;
  linkResultFolderPath?: string | null;
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
  let metadataRequestNumber = 0;
  const getMaterialMetadata = vi.fn(async () => {
    metadataRequestNumber += 1;
    if (metadataRequestNumber === 1 && options.initialMetadataPromise)
      return options.initialMetadataPromise;
    return metadata;
  });
  let snapshotRequestNumber = 0;
  const getMaterialMetadataSnapshots = vi.fn(async () => {
    snapshotRequestNumber += 1;
    if (snapshotRequestNumber === 1 && options.initialSnapshotsPromise)
      return options.initialSnapshotsPromise;
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
    material = {
      ...material,
      folderPath: options.linkResultFolderPath === undefined
        ? folderPath
        : options.linkResultFolderPath,
    };
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

  it("rejects an invalid relative path before HTTP, labels the input error and invalidates an old preflight", async () => {
    const { preflightMaterialFolder } = await renderDetail();
    const input = screen.getByLabelText("Relative folder path");
    fireEvent.change(input, { target: { value: "  brand/../secret  " } });
    fireEvent.click(screen.getByRole("button", { name: "Check folder" }));

    expect(preflightMaterialFolder).not.toHaveBeenCalled();
    expect(input).toHaveValue("brand/../secret");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAttribute("aria-describedby", "material-folder-path-error");
    expect(screen.getByText(/must not contain/)).toHaveAttribute(
      "id",
      "material-folder-path-error",
    );
    expect(input).toHaveFocus();

    const validPath = `library/${materialDto.technical_identity}`;
    fireEvent.change(input, { target: { value: validPath } });
    expect(input).toHaveAttribute("aria-invalid", "false");
    expect(input).not.toHaveAttribute("aria-describedby");
    expect(screen.queryByText(/must not contain/)).not.toBeInTheDocument();

    fireEvent.change(input, { target: { value: `  ${validPath}  ` } });
    fireEvent.click(screen.getByRole("button", { name: "Check folder" }));
    await screen.findByLabelText("Folder check result");
    expect(input).toHaveValue(validPath);
    expect(preflightMaterialFolder).toHaveBeenCalledWith(materialDto.id, validPath);
    expect(screen.getByRole("button", { name: "Link folder" })).toBeEnabled();

    fireEvent.change(input, { target: { value: `${validPath}-changed` } });
    expect(screen.queryByLabelText("Folder check result")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Link folder" })).not.toBeInTheDocument();
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

  it("confirms and links a folder, then reloads detail, current metadata and snapshots", async () => {
    const {
      linkMaterialFolder,
      preflightMaterialFolder,
      getMaterial,
      getMaterialMetadata,
      getMaterialMetadataSnapshots,
    } = await renderDetail();
    expect(await screen.findByText("not scanned")).toBeInTheDocument();
    expect(await screen.findByText("No metadata snapshots yet.")).toBeInTheDocument();

    const linkedSnapshot = materialMetadataSnapshotFromDto({
      ...snapshotDto,
      id: "60000000-0000-4000-8000-000000000007",
      sequence_number: 7,
      status: "WARNING",
      source_filename: "linked-snapshot.txt",
      loaded_at: "2026-09-11T14:07:00Z",
      created_at: "2026-09-11T14:07:01Z",
    });
    const linkedMetadata = materialMetadataFromDto({
      ...metadataDto,
      current_snapshot_id: linkedSnapshot.id,
      status: "WARNING",
      source_filename: "linked-current.txt",
      loaded_at: "2026-09-11T14:08:00Z",
    });
    getMaterialMetadata.mockResolvedValueOnce(linkedMetadata);
    getMaterialMetadataSnapshots.mockResolvedValueOnce([linkedSnapshot]);

    const linkedPath = `library/${materialDto.technical_identity}`;
    const folderInput = screen.getByLabelText("Relative folder path");
    fireEvent.change(folderInput, { target: { value: `  ${linkedPath}  ` } });
    fireEvent.click(screen.getByRole("button", { name: "Check folder" }));
    await screen.findByLabelText("Folder check result");
    expect(folderInput).toHaveValue(linkedPath);
    expect(preflightMaterialFolder).toHaveBeenLastCalledWith(materialDto.id, linkedPath);
    await openAndConfirm("Link folder", "Link this folder?");
    expect(await screen.findByText(/was linked successfully/)).toBeInTheDocument();
    expect(linkMaterialFolder).toHaveBeenCalledTimes(1);
    expect(linkMaterialFolder).toHaveBeenCalledWith(materialDto.id, linkedPath);
    expect(getMaterial).toHaveBeenCalledTimes(2);
    expect(getMaterial).toHaveBeenLastCalledWith(materialDto.id);
    expect(getMaterialMetadata).toHaveBeenCalledTimes(2);
    expect(getMaterialMetadata).toHaveBeenLastCalledWith(materialDto.id);
    expect(getMaterialMetadataSnapshots).toHaveBeenCalledTimes(2);
    expect(getMaterialMetadataSnapshots).toHaveBeenLastCalledWith(materialDto.id);
    expect(screen.getByText("Folder status").nextElementSibling).toHaveTextContent("Linked");
    expect(screen.getByText("Folder path").nextElementSibling).toHaveTextContent(
      linkedPath,
    );
    expect(screen.queryByLabelText("Folder check result")).not.toBeInTheDocument();

    const currentPanel = screen.getByRole("heading", { name: "Current metadata" }).closest("article");
    const historyPanel = screen.getByRole("heading", { name: "Snapshot history" }).closest("article");
    expect(currentPanel).not.toBeNull();
    expect(historyPanel).not.toBeNull();
    expect(within(currentPanel!).getByText("warning")).toBeInTheDocument();
    expect(within(currentPanel!).getByText("linked-current.txt")).toBeInTheDocument();
    expect(within(currentPanel!).getByText("2026-09-11T14:08:00Z")).toBeInTheDocument();
    expect(within(currentPanel!).queryByText("not scanned")).not.toBeInTheDocument();
    expect(within(historyPanel!).getByText("Snapshot 7")).toBeInTheDocument();
    expect(within(historyPanel!).getAllByText("2026-09-11T14:07:00Z").length).toBeGreaterThan(0);
    expect(within(historyPanel!).queryByText("No metadata snapshots yet.")).not.toBeInTheDocument();
  });

  it("never exposes an unsafe path returned by folder-link", async () => {
    const unsafePath = "C:\\private\\materials\\secret";
    await renderDetail({ linkResultFolderPath: unsafePath });
    await runPreflight();
    await openAndConfirm("Link folder", "Link this folder?");

    expect(await screen.findByText(/was linked successfully/)).toBeInTheDocument();
    expect(screen.queryByText(unsafePath)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Relative folder path")).toHaveValue(
      `library/${materialDto.technical_identity}`,
    );
    expect(screen.getByText("Folder path").nextElementSibling).toHaveTextContent("unsafe path hidden");
  });

  it("prevents a double folder-link submission while the first request is pending", async () => {
    let reject!: (reason: unknown) => void;
    const pending = new Promise<never>((_resolve, rejectPromise) => { reject = rejectPromise; });
    const { linkMaterialFolder } = await renderDetail({ linkPromise: pending });
    await runPreflight();
    fireEvent.click(screen.getByRole("button", { name: "Link folder" }));
    const dialog = screen.getByRole("dialog", { name: "Link this folder?" });
    const confirm = within(dialog).getByRole("button", { name: "Link folder" });

    act(() => {
      confirm.click();
      confirm.click();
    });

    expect(linkMaterialFolder).toHaveBeenCalledTimes(1);
    expect(within(dialog).getByRole("button", { name: "Linking…" })).toBeDisabled();
    await act(async () => reject(new ApiError(503, "Unavailable")));
  });

  it("keeps a successful link visible when a subsequent refresh fails and retries all data", async () => {
    const result = await renderDetail();
    await screen.findByText("not scanned");
    await screen.findByText("No metadata snapshots yet.");
    const refreshedSnapshot = materialMetadataSnapshotFromDto({
      ...snapshotDto,
      id: "60000000-0000-4000-8000-000000000006",
      sequence_number: 6,
      source_filename: "linked-history.txt",
      loaded_at: "2026-09-11T16:06:00Z",
      created_at: "2026-09-11T16:06:01Z",
    });
    result.getMaterialMetadata.mockRejectedValueOnce(new TypeError("Network unavailable"));
    result.getMaterialMetadataSnapshots.mockResolvedValueOnce([refreshedSnapshot]);

    await runPreflight();
    await openAndConfirm("Link folder", "Link this folder?");

    const savedButStale = await screen.findByText(/The folder was linked, but some updated data could not be reloaded/);
    expect(savedButStale).toBeInTheDocument();
    expect(savedButStale.closest("[role=alert]")).toHaveFocus();
    expect(result.linkMaterialFolder).toHaveBeenCalledTimes(1);
    expect(result.getMaterial).toHaveBeenCalledTimes(2);
    expect(result.getMaterialMetadata).toHaveBeenCalledTimes(2);
    expect(result.getMaterialMetadataSnapshots).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Folder status").nextElementSibling).toHaveTextContent("Linked");
    expect(screen.getByText("Folder path").nextElementSibling).toHaveTextContent(
      `library/${materialDto.technical_identity}`,
    );
    const historyPanel = screen.getByRole("heading", { name: "Snapshot history" }).closest("article");
    expect(historyPanel).not.toBeNull();
    expect(within(historyPanel!).getByText("Snapshot 6")).toBeInTheDocument();
    expect(within(historyPanel!).queryByText("No metadata snapshots yet.")).not.toBeInTheDocument();

    const retryMetadata = materialMetadataFromDto({
      ...metadataDto,
      current_snapshot_id: refreshedSnapshot.id,
      status: "VALID",
      source_filename: "retry-current.txt",
      loaded_at: "2026-09-11T16:07:00Z",
    });
    let resolveRetry!: (value: MaterialMetadata) => void;
    const pendingRetry = new Promise<MaterialMetadata>((resolve) => { resolveRetry = resolve; });
    result.getMaterialMetadata.mockReturnValueOnce(pendingRetry);
    result.getMaterialMetadataSnapshots.mockResolvedValueOnce([refreshedSnapshot]);
    const reload = screen.getByRole("button", { name: "Reload material data" });
    const panelRetry = screen.getByRole("button", { name: "Try again" });
    act(() => {
      reload.click();
      panelRetry.click();
    });
    expect(screen.getByRole("button", { name: "Reloading…" })).toBeDisabled();
    await waitFor(() => {
      expect(result.getMaterial).toHaveBeenCalledTimes(3);
      expect(result.getMaterialMetadata).toHaveBeenCalledTimes(3);
      expect(result.getMaterialMetadataSnapshots).toHaveBeenCalledTimes(3);
    });
    await act(async () => resolveRetry(retryMetadata));

    expect(await screen.findByText(/Material detail, current metadata and snapshot history were reloaded/)).toBeInTheDocument();
    expect(result.getMaterial).toHaveBeenCalledTimes(3);
    expect(result.getMaterialMetadata).toHaveBeenCalledTimes(3);
    expect(result.getMaterialMetadataSnapshots).toHaveBeenCalledTimes(3);
    expect(result.linkMaterialFolder).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/Change saved, refresh incomplete/)).not.toBeInTheDocument();
    const currentPanel = screen.getByRole("heading", { name: "Current metadata" }).closest("article");
    expect(currentPanel).not.toBeNull();
    expect(within(currentPanel!).getByText("retry-current.txt")).toBeInTheDocument();
    expect(within(currentPanel!).getByText("2026-09-11T16:07:00Z")).toBeInTheDocument();
    expect(within(historyPanel!).getByText("Snapshot 6")).toBeInTheDocument();
  });

  it("does not let older metadata or snapshot requests overwrite the post-link refresh", async () => {
    let resolveInitial!: (value: MaterialMetadata) => void;
    let resolveInitialSnapshots!: (value: MaterialMetadataSnapshot[]) => void;
    const initialMetadata = new Promise<MaterialMetadata>((resolve) => { resolveInitial = resolve; });
    const initialSnapshots = new Promise<MaterialMetadataSnapshot[]>((resolve) => {
      resolveInitialSnapshots = resolve;
    });
    const result = await renderDetail({
      initialMetadataPromise: initialMetadata,
      initialSnapshotsPromise: initialSnapshots,
    });
    await waitFor(() => expect(result.getMaterialMetadata).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.getMaterialMetadataSnapshots).toHaveBeenCalledTimes(1));
    const refreshedMetadata = materialMetadataFromDto({
      ...metadataDto,
      status: "VALID",
      source_filename: "fresh-current.txt",
      loaded_at: "2026-09-11T13:00:00Z",
    });
    const refreshedSnapshot = materialMetadataSnapshotFromDto({
      ...snapshotDto,
      id: "60000000-0000-4000-8000-000000000008",
      sequence_number: 8,
      source_filename: "fresh-history.txt",
      loaded_at: "2026-09-11T13:01:00Z",
      created_at: "2026-09-11T13:01:01Z",
    });
    result.getMaterialMetadata.mockResolvedValueOnce(refreshedMetadata);
    result.getMaterialMetadataSnapshots.mockResolvedValueOnce([refreshedSnapshot]);

    await runPreflight();
    await openAndConfirm("Link folder", "Link this folder?");
    await screen.findByText(/was linked successfully/);
    const currentPanel = screen.getByRole("heading", { name: "Current metadata" }).closest("article");
    expect(currentPanel).not.toBeNull();
    expect(within(currentPanel!).getByText("valid")).toBeInTheDocument();
    const historyPanel = screen.getByRole("heading", { name: "Snapshot history" }).closest("article");
    expect(historyPanel).not.toBeNull();
    expect(within(historyPanel!).getByText("Snapshot 8")).toBeInTheDocument();

    await act(async () => {
      resolveInitial(materialMetadataFromDto(metadataDto));
      resolveInitialSnapshots([materialMetadataSnapshotFromDto(snapshotDto)]);
    });
    await waitFor(() => expect(within(currentPanel!).getByText("valid")).toBeInTheDocument());
    expect(within(currentPanel!).queryByText("not scanned")).not.toBeInTheDocument();
    expect(within(historyPanel!).getByText("Snapshot 8")).toBeInTheDocument();
    expect(within(historyPanel!).queryByText("Snapshot 1")).not.toBeInTheDocument();
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
    const { markMaterialDone, getMaterial, getMaterialMetadata, getMaterialMetadataSnapshots } = await renderDetail({ linked: true, metadata: current });
    expect(await screen.findByText(status === "VALID" ? "valid" : "missing")).toBeInTheDocument();
    await screen.findByText("No metadata snapshots yet.");

    const sequenceNumber = status === "VALID" ? 9 : 10;
    const minute = String(sequenceNumber).padStart(2, "0");
    const loadedAt = `2026-09-11T15:${minute}:00Z`;
    const doneSnapshot = materialMetadataSnapshotFromDto({
      ...snapshotDto,
      id: `60000000-0000-4000-8000-${String(sequenceNumber).padStart(12, "0")}`,
      sequence_number: sequenceNumber,
      status,
      source_filename: status === "VALID" ? "done-current.txt" : null,
      warnings: warning,
      loaded_at: loadedAt,
      created_at: `2026-09-11T15:${minute}:01Z`,
    });
    const refreshedCurrent = materialMetadataFromDto({
      ...metadataDto,
      current_snapshot_id: doneSnapshot.id,
      status,
      source_filename: status === "VALID" ? "done-current.txt" : null,
      warnings: warning,
      loaded_at: loadedAt,
    });
    getMaterialMetadata.mockResolvedValueOnce(refreshedCurrent);
    getMaterialMetadataSnapshots.mockResolvedValueOnce([doneSnapshot]);

    fireEvent.click(screen.getByRole("button", { name: "Mark as Done" }));
    const dialog = screen.getByRole("dialog", { name: "Mark this material as Done?" });
    expect(dialog).toHaveTextContent("does not by itself prevent");
    fireEvent.click(within(dialog).getByRole("button", { name: "Mark as Done" }));
    expect(await screen.findByText(/Material was marked as Done/)).toBeInTheDocument();
    expect(markMaterialDone).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(getMaterial).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getMaterialMetadata).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getMaterialMetadataSnapshots).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("button", { name: "Mark as Done" })).not.toBeInTheDocument();
    const currentPanel = screen.getByRole("heading", { name: "Current metadata" }).closest("article");
    const historyPanel = screen.getByRole("heading", { name: "Snapshot history" }).closest("article");
    expect(currentPanel).not.toBeNull();
    expect(historyPanel).not.toBeNull();
    expect(within(currentPanel!).getByText(loadedAt)).toBeInTheDocument();
    expect(within(historyPanel!).getByText(`Snapshot ${sequenceNumber}`)).toBeInTheDocument();
    expect(within(historyPanel!).queryByText("No metadata snapshots yet.")).not.toBeInTheDocument();
    if (status === "MISSING")
      expect(screen.getAllByText(/Metadata file is missing/).length).toBeGreaterThan(0);
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
  it("shows INVALID current metadata and its normalized issue", async () => {
    const metadata = materialMetadataFromDto({
      ...metadataDto,
      status: "INVALID",
      source_filename: "metadata.txt",
      loaded_at: "2026-09-11T12:45:00Z",
      warnings: [{
        code: "METADATA_JSON_INVALID",
        message: "The metadata file is not valid JSON.",
        path: "library/metadata.txt",
      }],
    });
    await renderDetail({ metadata });

    const panel = screen.getByRole("heading", { name: "Current metadata" }).closest("article");
    expect(panel).not.toBeNull();
    expect(await within(panel!).findByText("invalid")).toBeInTheDocument();
    expect(within(panel!).getByText(/METADATA_JSON_INVALID/).closest("li")).toHaveTextContent(
      "The metadata file is not valid JSON.",
    );
    expect(within(panel!).getByText(/library\/metadata.txt/)).toBeInTheDocument();
  });

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
