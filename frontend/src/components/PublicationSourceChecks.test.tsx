import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PublicationSourceChecks } from "./PublicationSourceChecks";
import { technicalClient, type TechnicalReview } from "../api/technicalClient";
import { requestNavigation } from "../navigationGuard";

const ids = ["00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"];
const current: TechnicalReview = { review: { generation: 3, revisionHash: "a".repeat(64), inventoryId: ids[0], checkedAt: null, failureCode: null }, validation: null, approvals: [] };
const checked: TechnicalReview = { ...current, validation: { id: ids[0], actorId: ids[0], createdAt: "2026-01-01", reportHash: "a".repeat(64), canApprove: true, errors: [], warnings: [], images: [] } };
afterEach(() => vi.restoreAllMocks());

it("runs source checks sequentially and opens the preview without human approval calls", async () => {
  vi.spyOn(technicalClient, "current").mockResolvedValue(current);
  const run = vi.spyOn(technicalClient, "prepare").mockResolvedValue(checked), approve = vi.spyOn(technicalClient, "approve");
  const onChecked = vi.fn().mockResolvedValue(undefined);
  render(<PublicationSourceChecks materialIds={ids} disabled={false} onBusyChange={vi.fn()} onChecked={onChecked} />);
  fireEvent.click(screen.getByRole("button", { name: "Check sources and review" }));
  await waitFor(() => expect(onChecked).toHaveBeenCalledOnce());
  expect(run.mock.calls.map(call => call[0])).toEqual(ids);
  expect(run.mock.calls.every(call => call[1] === 3)).toBe(true);
  expect(approve).not.toHaveBeenCalled();
});

it("freezes the selection after an unknown source check and recovers its exact key", async () => {
  vi.spyOn(technicalClient, "current").mockResolvedValue(current);
  const run = vi.spyOn(technicalClient, "prepare").mockRejectedValueOnce(new TypeError("Lost response")).mockResolvedValue(checked);
  const onChecked = vi.fn().mockResolvedValue(undefined);
  render(<PublicationSourceChecks materialIds={ids} disabled={false} onBusyChange={vi.fn()} onChecked={onChecked} />);
  fireEvent.click(screen.getByRole("button", { name: "Check sources and review" }));
  await screen.findByRole("button", { name: "Recover same source check" });
  expect(run).toHaveBeenCalledTimes(1); expect(requestNavigation("/materials")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Recover same source check" }));
  await waitFor(() => expect(onChecked).toHaveBeenCalledOnce());
  expect(run.mock.calls[0]).toEqual(run.mock.calls[1]); expect(run.mock.calls[2][0]).toBe(ids[1]);
  expect(requestNavigation("/materials")).toBe(true);
});

it("does not check later materials or open a preview after failed image validation", async () => {
  vi.spyOn(technicalClient, "current").mockResolvedValue(current);
  const run = vi.spyOn(technicalClient, "prepare").mockResolvedValue({ ...checked, validation: { ...checked.validation!, canApprove: false, errors: [{ code: "IMAGE_UNREADABLE", path: "4K/map.png" }] } });
  const onChecked = vi.fn().mockResolvedValue(undefined);
  render(<PublicationSourceChecks materialIds={ids} disabled={false} onBusyChange={vi.fn()} onChecked={onChecked} />);
  fireEvent.click(screen.getByRole("button", { name: "Check sources and review" }));
  await screen.findByText(/Source check failed/);
  expect(run).toHaveBeenCalledTimes(1); expect(onChecked).not.toHaveBeenCalled();
});
