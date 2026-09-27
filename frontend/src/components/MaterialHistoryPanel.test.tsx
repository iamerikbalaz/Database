import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MaterialHistoryPanel } from "./MaterialHistoryPanel";
import { materialDto, processorDto } from "../test/materialFixtures";

afterEach(() => vi.unstubAllGlobals());
it("shows actual authors and content changes in one paginated history", async () => {
  const event = { id: `content:${processorDto.id}`, source: "content", action: "CONTENT", created_at: materialDto.updated_at,
    author: { id: processorDto.id, display_name: "Actual editor" }, summary: "Content saved", reason: null,
    changes: [{ field: "description", label: "Description", before: "Before", after: "After" }] };
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ material_id: materialDto.id, items: [event], next_cursor: event.id })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ material_id: materialDto.id, items: [{ ...event, id: "metadata:older", source: "metadata", author: null, summary: "Metadata saved" }], next_cursor: null })));
  vi.stubGlobal("fetch", fetch);
  render(<MaterialHistoryPanel id={materialDto.id} updatedAt={materialDto.updated_at} />);
  expect(fetch).not.toHaveBeenCalled(); fireEvent.click(screen.getByText("Material record change history"));
  fireEvent.click(await screen.findByText(/Content saved/)); expect(screen.getByText("Actual editor")).toBeVisible();
  expect(screen.getByText("After", { selector: "pre" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Older changes" }));
  fireEvent.click(await screen.findByText(/Metadata saved/)); expect(screen.getByText("Author not recorded")).toBeVisible();
  expect(fetch.mock.calls[1][0]).toContain(encodeURIComponent(event.id));
  expect(screen.getByRole("button", { name: "Older changes" })).toBeDisabled();
});

it("refreshes an open history after a save even when the material timestamp is unchanged", async () => {
  const event = { id: "content:new", source: "content", action: "CONTENT", created_at: materialDto.updated_at,
    author: { id: processorDto.id, display_name: "Actual editor" }, summary: "Content saved", reason: null, changes: [] };
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ material_id: materialDto.id, items: [], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ material_id: materialDto.id, items: [event], next_cursor: null })));
  vi.stubGlobal("fetch", fetch);
  const { rerender } = render(<MaterialHistoryPanel id={materialDto.id} updatedAt={materialDto.updated_at} refreshRevision={0} />);
  fireEvent.click(screen.getByText("Material record change history"));
  await screen.findByText("No recorded changes.");
  rerender(<MaterialHistoryPanel id={materialDto.id} updatedAt={materialDto.updated_at} refreshRevision={1} />);
  expect(await screen.findByText(/Content saved/)).toBeVisible();
  expect(screen.queryByText("No recorded changes.")).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(2);
});
