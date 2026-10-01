import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import { PreviewLightbox } from "./PreviewLightbox";
import { MaterialGallery } from "./MaterialGallery";
import { MaterialPreviewStrip } from "./MaterialPreviewStrip";
import { previewClient } from "../api/previewClient";
import { GalleryStore } from "../api/galleryStore";
import { materialFromDto } from "../api/materialDto";
import { materialDto } from "../test/materialFixtures";

const entries = ["SPHERE_1.png", "WALL_2.png", "ROOM_3.png"].map((name, index) => ({ name, sha256: String(index + 1).repeat(64), size: 50 }));
const image = () => ({ blob: new Blob(["synthetic PNG"], { type: "image/png" }), width: 1200, height: 1200 });
const createUrl = vi.fn(), revokeUrl = vi.fn();
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  createUrl.mockReset().mockImplementation(() => `blob:original-${createUrl.mock.calls.length}`); revokeUrl.mockReset();
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createUrl });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeUrl });
  vi.spyOn(previewClient, "original").mockImplementation(async () => image());
});
afterEach(() => vi.restoreAllMocks());
const mount = (onClose = vi.fn()) => render(<PreviewLightbox materialId={materialDto.id} entries={entries} selectedName="WALL_2.png" onClose={onClose} />);

it("loads the selected original on demand, cycles in gallery order and supports actual pixels", async () => {
  mount();
  expect(await screen.findByRole("img")).toHaveAttribute("alt", "Full-quality preview: WALL_2.png");
  expect(previewClient.original).toHaveBeenCalledWith(materialDto.id, entries[1], expect.any(AbortSignal));
  expect(screen.getByText("WALL_2.png · 1200 × 1200 px · Full quality")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "1:1 pixels" }));
  expect(screen.getByRole("img")).toHaveStyle({ width: "1200px", height: "1200px" });
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "ArrowRight" });
  await screen.findByRole("img", { name: "Full-quality preview: ROOM_3.png" });
  fireEvent.click(screen.getByRole("button", { name: "Next original preview" }));
  await screen.findByRole("img", { name: "Full-quality preview: SPHERE_1.png" });
  fireEvent.click(screen.getByRole("button", { name: "Previous original preview" }));
  await screen.findByRole("img", { name: "Full-quality preview: ROOM_3.png" });
  expect(revokeUrl).toHaveBeenCalledWith("blob:original-1");
});

it("aborts a superseded request, ignores its late response and cleans up pixels", async () => {
  let resolve!: (value: ReturnType<typeof image>) => void;
  vi.mocked(previewClient.original).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const view = mount(); await waitFor(() => expect(previewClient.original).toHaveBeenCalledOnce());
  const signal = vi.mocked(previewClient.original).mock.calls[0][2];
  fireEvent.click(screen.getByRole("button", { name: "Next original preview" }));
  await screen.findByRole("img", { name: "Full-quality preview: ROOM_3.png" });
  expect(signal.aborted).toBe(true);
  await act(async () => resolve(image())); expect(createUrl).toHaveBeenCalledOnce();
  view.unmount(); expect(revokeUrl).toHaveBeenCalledWith("blob:original-1");
});

it("closes on native Escape cancellation and restores focus to the trigger", async () => {
  const close = vi.fn(), trigger = document.createElement("button"); document.body.appendChild(trigger); trigger.focus();
  const view = mount(close); await screen.findByRole("img");
  fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true })); expect(close).toHaveBeenCalledOnce();
  view.unmount(); expect(document.activeElement).toBe(trigger); trigger.remove();
});

it("handles source and browser decode failures without reflecting private diagnostics", async () => {
  vi.mocked(previewClient.original).mockRejectedValueOnce(new Error("PRIVATE_PATH"));
  mount(); expect(await screen.findByRole("alert")).not.toHaveTextContent("PRIVATE_PATH");
  fireEvent.click(screen.getByRole("button", { name: "Retry original preview" }));
  fireEvent.error(await screen.findByRole("img")); await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Retry original preview" })); await screen.findByRole("img");
  expect(revokeUrl).toHaveBeenCalledWith("blob:original-1");
});

it("avoids duplicate original reads in StrictMode", async () => {
  render(<StrictMode><PreviewLightbox materialId={materialDto.id} entries={entries} selectedName={entries[0].name} onClose={vi.fn()} /></StrictMode>);
  await screen.findByRole("img"); expect(previewClient.original).toHaveBeenCalledOnce();
});

it("opens the detail gallery at its current image without fetching originals beforehand", async () => {
  vi.spyOn(previewClient, "listing").mockResolvedValue({ items: entries, missing: false, ignoredEntries: 0 });
  vi.spyOn(previewClient, "image").mockResolvedValue(image());
  render(<MaterialGallery materialId={materialDto.id} linked />);
  await screen.findByRole("img"); expect(previewClient.original).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Next preview" }));
  fireEvent.click(await screen.findByRole("button", { name: "Open WALL_2.png in full quality" }));
  expect(await within(screen.getByRole("dialog")).findByRole("img")).toHaveAttribute("alt", "Full-quality preview: WALL_2.png");
});

it("opens the clicked expanded thumbnail and keeps row selection untouched", async () => {
  const row = { ...materialFromDto(materialDto), folderPath: "TEST/TEST_0001_SAMPLE_K03" }, store = new GalleryStore(), select = vi.fn(), edit = vi.fn();
  vi.spyOn(store, "listing").mockResolvedValue({ items: entries, missing: false, ignoredEntries: 0 });
  vi.spyOn(store, "image").mockResolvedValue(image());
  render(<div onClick={select}><MaterialPreviewStrip material={row} store={store} editable onEdit={edit} onCount={vi.fn()} /></div>);
  fireEvent.click(await screen.findByRole("button", { name: `Open WALL_2.png from ${row.materialName} in full quality` }));
  expect(await within(screen.getByRole("dialog")).findByRole("img")).toHaveAttribute("alt", "Full-quality preview: WALL_2.png");
  fireEvent.click(screen.getByRole("button", { name: "Next original preview" }));
  await within(screen.getByRole("dialog")).findByRole("img", { name: "Full-quality preview: SPHERE_1.png" });
  expect(select).not.toHaveBeenCalled(); expect(edit).not.toHaveBeenCalled();
});
