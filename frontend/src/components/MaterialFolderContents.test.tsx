import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MaterialFolderContents } from "./MaterialFolderContents";

const id = "10000000-0000-4000-8000-000000000001";
const response = (path = "", entries: unknown[] = [{ name: "PREVIEW", path: "PREVIEW", kind: "directory", size: 0 }]) =>
  new Response(JSON.stringify({ material_id: id, path, omitted_entries: 0, entries }), { headers: { "Content-Type": "application/json" } });
afterEach(() => vi.unstubAllGlobals());

it("loads the root once and lazily expands child directories within the material", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response()).mockResolvedValueOnce(response("PREVIEW", [{ name: "SPHERE_1.png", path: "PREVIEW/SPHERE_1.png", kind: "file", size: 2048 }]));
  vi.stubGlobal("fetch", fetch);
  render(<MaterialFolderContents materialId={id} folderPath="brand/MATERIAL" />);
  fireEvent.click(await screen.findByText("PREVIEW"));
  expect(await screen.findByText("SPHERE_1.png")).toBeVisible();
  expect(screen.getByText("2 KB")).toBeVisible();
  expect(fetch.mock.calls[1][0]).toBe(`/api/materials/${id}/folder-contents?path=PREVIEW`);
  expect(screen.queryByRole("button", { name: "Parent folder" })).not.toBeInTheDocument();
  expect(screen.getByText("PREVIEW").closest("details")).toHaveAttribute("open");
});

it("does not request an unlinked folder", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  render(<MaterialFolderContents materialId={id} folderPath={null} />);
  expect(await screen.findByText("No source folder linked.")).toBeVisible();
  expect(fetch).not.toHaveBeenCalled();
});

it("rejects unsafe or unrelated entry paths without displaying them", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response("", [{ name: "private", path: "../private", kind: "file", size: 1 }])));
  render(<MaterialFolderContents materialId={id} folderPath="brand/MATERIAL" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Folder contents are unavailable");
  expect(screen.queryByText("private", { exact: true })).not.toBeInTheDocument();
});

it("shows a useful offline message and can retry", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response("{}", { status: 503 })).mockResolvedValueOnce(response("", []));
  vi.stubGlobal("fetch", fetch); render(<MaterialFolderContents materialId={id} folderPath="brand/MATERIAL" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("source connection may be offline");
  fireEvent.click(screen.getByRole("button", { name: "Refresh contents" }));
  await waitFor(() => expect(screen.getByText("This folder is empty.")).toBeVisible());
});
