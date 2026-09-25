import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialsGrid } from "./MaterialsGrid";
import { GalleryStore } from "../api/galleryStore";
import { previewClient } from "../api/previewClient";
import { materialDto } from "../test/materialFixtures";
import { materialFromDto } from "../api/materialDto";

const material = materialFromDto({ ...materialDto, folder_path: "brand/" + materialDto.technical_identity });
const entries = ["OTHER.png", "SPHERE_1.png", "FABRIC_1.png"].map((name, i) => ({ name, size: 20, sha256: String(i + 1).repeat(64) }));
const visibility: ((near: boolean) => void)[] = [];
beforeEach(() => {
  visibility.length = 0;
  vi.stubGlobal("IntersectionObserver", class {
    constructor(callback: (entries: { isIntersecting: boolean }[]) => void) { visibility.push(near => callback([{ isIntersecting: near }])); }
    observe() {} disconnect() {}
  });
  vi.spyOn(previewClient, "listing").mockResolvedValue({ missing: false, ignoredEntries: 0, items: entries });
  vi.spyOn(previewClient, "image").mockResolvedValue({ blob: new Blob(["pixels"]), width: 512, height: 512 });
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:preview") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const near = (value = true) => act(() => visibility.forEach(callback => callback(value)));
it("loads only near the viewport, prioritizes the primary image and cycles without navigation", async () => {
  const navigate = vi.fn();
  render(<StrictMode><MaterialsGrid materials={[material]} store={new GalleryStore()} size="medium" navigate={navigate} /></StrictMode>);
  expect(previewClient.listing).not.toHaveBeenCalled(); near();
  await screen.findByRole("img", { name: /FABRIC_1.png/ });
  expect(previewClient.listing).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: /Next preview/ })); await screen.findByRole("img", { name: /SPHERE_1.png/ });
  expect(navigate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /Previous preview/ })); await screen.findByRole("img", { name: /FABRIC_1.png/ });
  expect(previewClient.image).toHaveBeenCalledTimes(2);
  near(false); expect(screen.queryByRole("img")).not.toBeInTheDocument(); expect(URL.revokeObjectURL).toHaveBeenCalled();
  near(); await screen.findByRole("img", { name: /FABRIC_1.png/ }); expect(previewClient.image).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("link", { name: `Open ${material.materialName}` })); expect(navigate).toHaveBeenCalledWith(`/materials/${material.id}`);
});
it("shows unlinked and empty materials without image requests", async () => {
  vi.mocked(previewClient.listing).mockResolvedValue({ missing: false, ignoredEntries: 0, items: [] });
  render(<MaterialsGrid materials={[{ ...material, id: "unlinked", folderPath: null }, material]} store={new GalleryStore()} size="small" navigate={vi.fn()} />);
  near(); await screen.findByText("No PNG previews"); expect(screen.getByText("No folder linked")).toBeVisible(); expect(previewClient.image).not.toHaveBeenCalled();
});
it("sanitizes errors, retries and aborts pending work when scrolled away", async () => {
  vi.mocked(previewClient.listing).mockRejectedValueOnce(new Error("PRIVATE_MARKER"));
  render(<MaterialsGrid materials={[material]} store={new GalleryStore()} size="small" navigate={vi.fn()} />);
  near(); await screen.findByText("Preview unavailable"); expect(screen.queryByText(/PRIVATE_MARKER/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry preview" })); await screen.findByRole("img");
  expect(previewClient.image).toHaveBeenCalledWith(material.id, entries[2], expect.any(AbortSignal), 256);
  vi.mocked(previewClient.image).mockImplementationOnce(() => new Promise(() => {}));
  fireEvent.click(screen.getByRole("button", { name: /Next preview/ }));
  await waitFor(() => expect(previewClient.image).toHaveBeenCalledTimes(2));
  const signal = vi.mocked(previewClient.image).mock.calls[1][2]; near(false); expect(signal.aborted).toBe(true);
});
