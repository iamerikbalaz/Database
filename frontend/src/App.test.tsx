import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { mockApiClient } from "./api/client";
import { directoryClient } from "./api/directoryClient";
import { mockDirectory } from "./test/directoryFixtures";
beforeEach(mockDirectory);
afterEach(() => vi.restoreAllMocks());
describe("REAWOTE directory navigation", () => {
  it("navigates between Customers and Orders with canonical naming", async () => {
    render(<App client={mockApiClient} initialPath="/dashboard" />);
    await screen.findByRole("heading", { name: "Dashboard" });
    fireEvent.click(screen.getByRole("link", { name: "Customers" }));
    await screen.findByRole("link", { name: "Swisspearl" });
    expect(screen.getByRole("link", { name: "Customers" })).toHaveClass("active");
    fireEvent.click(screen.getByRole("link", { name: "Orders" }));
    expect(await screen.findByRole("heading", { name: "Orders" })).toBeVisible();
  });
  it("keeps old list URLs compatible and filters the single-level customer list", async () => {
    render(<App client={mockApiClient} initialPath="/companies" />);
    await screen.findByRole("link", { name: "Swisspearl" });
    expect(screen.getByRole("link", { name: "Lasvit" })).toBeVisible();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search customers" }), { target: { value: "Swiss" } });
    expect(screen.queryByRole("link", { name: "Lasvit" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Swisspearl" })).toHaveAttribute("href", expect.stringContaining("/customers/"));
  });
  it("shows an empty customer state", async () => {
    vi.mocked(directoryClient.customers).mockResolvedValue([]);
    render(<App client={mockApiClient} initialPath="/customers" />);
    expect(await screen.findByText("No customers match these filters.")).toBeVisible();
  });
  it("supports retry after a failed directory read", async () => {
    vi.mocked(directoryClient.customers).mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce([]);
    render(<App client={mockApiClient} initialPath="/customers" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Customers could not be loaded");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByText("No customers match these filters.");
    expect(directoryClient.customers).toHaveBeenCalledTimes(2);
  });
});
