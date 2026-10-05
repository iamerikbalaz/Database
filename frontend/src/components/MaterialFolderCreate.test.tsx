import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialFolderCreate } from "./MaterialFolderCreate";
import { materialCreationClient as api, type MaterialCreationResult } from "../api/materialCreationClient";
import { materialFromDto } from "../api/materialDto";
import { materialDto } from "../test/materialFixtures";
import { ApiError } from "../api/errors";
import { requestNavigation } from "../navigationGuard";

const material = materialFromDto(materialDto);
const result: MaterialCreationResult = { id: "60000000-0000-4000-8000-000000000001", status: "COMPLETED", completedCount: 1, totalCount: 1,
  items: [{ materialId: material.id, identity: material.technicalIdentity, name: material.materialName, folderPath: "SAFE/" + material.technicalIdentity, status: "COMPLETED", errorCode: null }] };
beforeEach(() => { sessionStorage.clear(); vi.spyOn(api, "options").mockResolvedValue({ pathsVersion: 4, canCreateFolders: true, templates: [] }); });
afterEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });

it("creates a folder for the existing record without requiring an SBS template", async () => {
  const create = vi.spyOn(api, "createFolder").mockResolvedValue(result), changed = vi.fn().mockResolvedValue(true);
  const records = vi.spyOn(api, "create");
  render(<MaterialFolderCreate material={material} onChanged={changed} />);
  const button = screen.getByRole("button", { name: "Create material folder" });
  await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
  expect(create).toHaveBeenCalledExactlyOnceWith(material.id, expect.objectContaining({ expected_updated_at: material.updatedAt, expected_paths_version: 4, template_name: null }));
  expect(records).not.toHaveBeenCalled(); expect(requestNavigation("/materials")).toBe(true);
});

it("never offers folder IO for a draft", async () => {
  const create = vi.spyOn(api, "createFolder");
  render(<MaterialFolderCreate material={{ ...material, isDraft: true, technicalIdentity: null, sequenceNumber: null, publishedBrandId: null }} onChanged={vi.fn()} />);
  expect(screen.getByText(/Complete Customer and Main category/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Create material folder" })).not.toBeInTheDocument();
  await waitFor(() => expect(api.options).toHaveBeenCalled());
  expect(create).not.toHaveBeenCalled();
});

it("persists unknown folder requests through remount and a later 409", async () => {
  const create = vi.spyOn(api, "createFolder").mockRejectedValueOnce(new TypeError("lost response")).mockRejectedValueOnce(new ApiError(409, "Try recovery")).mockResolvedValue(result);
  const changed = vi.fn().mockResolvedValue(true);
  const first = render(<MaterialFolderCreate material={material} onChanged={changed} />);
  const button = screen.getByRole("button", { name: "Create material folder" });
  await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  await screen.findByText(/result is unknown/);
  expect(requestNavigation("/materials")).toBe(false);
  first.unmount(); render(<MaterialFolderCreate material={material} onChanged={changed} />);
  fireEvent.click(screen.getByRole("button", { name: "Recover material folder creation" }));
  await screen.findByText(/result is unknown/);
  expect(requestNavigation("/materials")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Recover material folder creation" }));
  await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
  expect(create).toHaveBeenCalledTimes(3);
  expect(create.mock.calls[0]).toEqual(create.mock.calls[1]); expect(create.mock.calls[0]).toEqual(create.mock.calls[2]);
  expect(requestNavigation("/materials")).toBe(true);
});
