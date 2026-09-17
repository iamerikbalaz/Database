import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialGallery } from "./MaterialGallery";
import { previewClient } from "../api/previewClient";
import { materialDto } from "../test/materialFixtures";
import { StrictMode } from "react";

const entries = [{ name: "Front.png", size: 10, sha256: "a".repeat(64) }, { name: "Side.png", size: 20, sha256: "b".repeat(64) }];
const image = () => ({ blob: new Blob(["synthetic UI fixture"], { type: "image/jpeg" }), width: 512, height: 256 });
const createUrl = vi.fn(), revokeUrl = vi.fn();
beforeEach(() => {
  createUrl.mockReset().mockImplementation(() => `blob:synthetic-${createUrl.mock.calls.length}`); revokeUrl.mockReset();
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createUrl });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeUrl });
  vi.spyOn(previewClient, "listing").mockResolvedValue({ items: entries, missing: false, ignoredEntries: 0 });
  vi.spyOn(previewClient, "image").mockImplementation(async () => image());
});
afterEach(() => vi.restoreAllMocks());
const mount = (linked = true, initiallyOpen = false) => render(<MaterialGallery materialId={materialDto.id} linked={linked} initiallyOpen={initiallyOpen} />);

it("does not duplicate source reads or consume extra decoder slots in StrictMode", async () => {
  render(<StrictMode><MaterialGallery materialId={materialDto.id} linked initiallyOpen /></StrictMode>);
  await screen.findByRole("img");
  expect(previewClient.listing).toHaveBeenCalledOnce(); expect(previewClient.image).toHaveBeenCalledOnce();
});

it("reads sources only when opened and revokes the image when closed", async () => {
  mount(); expect(previewClient.listing).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Open preview gallery" }));
  expect(await screen.findByRole("img")).toHaveAttribute("alt", "Preview: Front.png");
  expect(previewClient.image).toHaveBeenCalledWith(materialDto.id, entries[0], expect.any(AbortSignal));
  const completedSignal = vi.mocked(previewClient.image).mock.calls[0][2];
  fireEvent.click(screen.getByRole("button", { name: "Close preview gallery" }));
  expect(revokeUrl).toHaveBeenCalledWith("blob:synthetic-1");
  expect(completedSignal.aborted).toBe(false);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
it("does not request an unlinked source", async () => {
  mount(false, true); expect(screen.getByText(/Link a material folder/)).toBeVisible();
  expect(previewClient.listing).not.toHaveBeenCalled(); expect(previewClient.image).not.toHaveBeenCalled();
});
it("changes selection without keeping the previous image alive", async () => {
  mount(true, true); await screen.findByRole("img");
  fireEvent.change(screen.getByLabelText("Preview image"), { target: { value: "Side.png" } });
  expect(await screen.findByRole("img", { name: "Preview: Side.png" })).toHaveAttribute("width", "512");
  expect(revokeUrl).toHaveBeenCalledWith("blob:synthetic-1");
});
it("ignores an old image response after a newer selection and aborts its request", async () => {
  let resolve!: (value: ReturnType<typeof image>) => void;
  vi.mocked(previewClient.image).mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  mount(true, true); await screen.findByLabelText("Preview image");
  await waitFor(() => expect(previewClient.image).toHaveBeenCalledOnce());
  const oldSignal = vi.mocked(previewClient.image).mock.calls[0][2];
  fireEvent.change(screen.getByLabelText("Preview image"), { target: { value: "Side.png" } });
  await screen.findByRole("img", { name: "Preview: Side.png" }); expect(oldSignal.aborted).toBe(true);
  await act(async () => resolve(image())); expect(createUrl).toHaveBeenCalledOnce();
  expect(screen.getByRole("img")).toHaveAttribute("alt", "Preview: Side.png");
});
it("reloads the source hash before showing a changed preview", async () => {
  mount(true, true); await screen.findByRole("img");
  const next = { ...entries[0], sha256: "c".repeat(64) };
  vi.mocked(previewClient.listing).mockResolvedValue({ items: [next], missing: false, ignoredEntries: 2 });
  fireEvent.click(screen.getByRole("button", { name: "Reload preview gallery" }));
  await waitFor(() => expect(previewClient.image).toHaveBeenLastCalledWith(materialDto.id, next, expect.any(AbortSignal)));
  expect(await screen.findByText(/2 other entries/)).toBeVisible(); expect(revokeUrl).toHaveBeenCalledWith("blob:synthetic-1");
});
it.each([true, false])("shows a missing or empty gallery without decoding (missing=%s)", async (missing) => {
  vi.mocked(previewClient.listing).mockResolvedValue({ items: [], missing, ignoredEntries: 0 });
  mount(true, true); await screen.findByText(missing ? /no PREVIEW folder/ : /No supported preview images/);
  expect(previewClient.image).not.toHaveBeenCalled();
});
it("handles listing errors without reflecting private diagnostics and can retry", async () => {
  vi.mocked(previewClient.listing).mockRejectedValueOnce(new Error("PRIVATE_SYNTHETIC_MARKER"));
  mount(true, true); expect(await screen.findByRole("alert")).not.toHaveTextContent("PRIVATE_SYNTHETIC_MARKER");
  fireEvent.click(screen.getByRole("button", { name: "Try again" })); await screen.findByRole("img");
});
it("handles transport and browser image decoding errors with retry", async () => {
  vi.mocked(previewClient.image).mockRejectedValueOnce(new Error("PRIVATE_SYNTHETIC_MARKER"));
  mount(true, true); expect(await screen.findByRole("alert")).not.toHaveTextContent("PRIVATE_SYNTHETIC_MARKER");
  fireEvent.click(screen.getByRole("button", { name: "Try again" })); fireEvent.error(await screen.findByRole("img"));
  await screen.findByRole("alert"); fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByRole("img"); expect(revokeUrl).toHaveBeenCalledWith("blob:synthetic-1");
});
