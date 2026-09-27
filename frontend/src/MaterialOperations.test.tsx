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
  return dialog;
}

describe("material folder and Done UI", () => {
  it("shows source folder controls without deleted metadata history panels", async () => {
    const result = await renderDetail();
    expect(screen.getByText("Folder path").nextElementSibling).toHaveTextContent("No folder linked");
    for (const name of ["Snapshot history", "Source inventory and review", "AI proposals", "Content approval"])
      expect(screen.queryByRole("heading", { name })).not.toBeInTheDocument();
    expect(result.getMaterialMetadataSnapshots).not.toHaveBeenCalled();
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

  it("confirms and links a folder then refreshes editable properties", async () => {
    const result = await renderDetail();
    const linkedPath = `library/${materialDto.technical_identity}`;
    await runPreflight(); await openAndConfirm("Link folder", "Link this folder?");
    expect(await screen.findByText(/was linked successfully/)).toBeInTheDocument();
    expect(result.linkMaterialFolder).toHaveBeenCalledOnce();
    expect(result.linkMaterialFolder).toHaveBeenCalledWith(materialDto.id, linkedPath);
    expect(result.getMaterial).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Folder path").nextElementSibling).toHaveTextContent(linkedPath);
    expect(screen.queryByLabelText("Folder check result")).not.toBeInTheDocument();
    expect(result.getMaterialMetadataSnapshots).not.toHaveBeenCalled();
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

  it("keeps a successful link visible after a failed refresh and recovers without relinking", async () => {
    const result = await renderDetail();
    result.getMaterial.mockRejectedValueOnce(new TypeError("Synthetic read failure"));
    await runPreflight(); await openAndConfirm("Link folder", "Link this folder?");
    await screen.findByText(/Change saved, but the material could not be reloaded/);
    expect(screen.getByText("Folder path").nextElementSibling).toHaveTextContent(`library/${materialDto.technical_identity}`);
    fireEvent.click(screen.getByRole("button", { name: "Reload material data" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Reload material data" })).not.toBeInTheDocument());
    expect(result.linkMaterialFolder).toHaveBeenCalledOnce(); expect(result.getMaterial).toHaveBeenCalledTimes(3);
  });

  it("shows and focuses a link conflict", async () => {
    await renderDetail({ linkError: new ApiError(409, "Conflict") });
    await runPreflight();
    const dialog = await openAndConfirm("Link folder", "Link this folder?");
    const error = (await screen.findByText(/already used or the material changed/)).closest("[role=alert]");
    expect(error).toHaveFocus();
    fireEvent(dialog, new Event("close"));
    await act(async () => { await Promise.resolve(); });
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
    const dialog = await openAndConfirm("Link folder", "Link this folder?");
    const errorText = await screen.findByText(message);
    const alert = errorText.closest("[role=alert]");
    expect(alert).toHaveFocus();
    fireEvent(dialog, new Event("close"));
    await act(async () => { await Promise.resolve(); });
    expect(alert).toHaveFocus();
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

  it.each(["Cancel", "Escape"])("closes confirmation with %s and restores focus", async (method) => {
    await renderDetail({ linked: true });
    await runPreflight();
    const trigger = screen.getByRole("button", { name: "Link folder" });
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Link this folder?" });
    if (method === "Cancel") fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    else fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(screen.queryByRole("dialog", { name: "Link this folder?" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
