import { render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OrderMaterialsPanel } from "./OrderMaterialsPanel";
import { mockApiClient } from "../api/client";
import { materialFromDto } from "../api/materialDto";
import { materialDto } from "../test/materialFixtures";

afterEach(() => vi.restoreAllMocks());
it("counts production progress and lists independent Checked and Automatic check values with previews", async () => {
  const first = { ...materialFromDto(materialDto), folderPath: null, materialName: "FIRST", workflowStatus: "DONE" as const, checkedStatus: "Correction", automaticFileCheckStatus: "OK" as const };
  const second = { ...first, id: "50000000-0000-4000-8000-000000000002", materialName: "SECOND", workflowStatus: "IN_PROGRESS" as const, checkedStatus: "no", automaticFileCheckStatus: "ISSUES" as const };
  const getMaterials = vi.fn().mockResolvedValue([first, second]);
  render(<OrderMaterialsPanel orderId="order-id" client={{ ...mockApiClient, getMaterials }} navigate={vi.fn()} />);
  const table = await screen.findByRole("table");
  expect(getMaterials).toHaveBeenCalledWith({ project_id: "order-id" });
  expect(screen.getByRole("progressbar", { name: "Order material progress" })).toHaveAttribute("value", "50");
  expect(screen.getByText("Assigned")).toHaveTextContent("2");
  const firstRow = screen.getByRole("link", { name: "FIRST" }).closest("tr")!;
  expect(within(firstRow).getByText("done")).toBeVisible(); expect(within(firstRow).getByText("Correction")).toBeVisible(); expect(within(firstRow).getByText("OK")).toBeVisible();
  expect(within(table).getAllByTitle("No folder linked")).toHaveLength(2);
});
it("keeps an order without materials usable with zero counts and progress", async () => {
  render(<OrderMaterialsPanel orderId="empty" client={{ ...mockApiClient, getMaterials: vi.fn().mockResolvedValue([]) }} navigate={vi.fn()} />);
  expect(await screen.findByText(/No materials assigned/)).toBeVisible();
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "0");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});
