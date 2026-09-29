import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { FileCheckProgress } from "./FileCheckProgress";
import type { FileCheckProgress as Progress } from "../api/materialCheckJobs";

const progress: Progress = { id: "60000000-0000-4000-8000-000000000001", status: "RUNNING", total: 50, completed: 12,
  active: [{ materialId: "50000000-0000-4000-8000-000000000001", identity: "ROUBAL_0001_TILES-ORANGE_B01", file: "4K/ROUBAL_0001_TILES-ORANGE_COL_4K.jpg", phase: "checking" }], elapsedSeconds: 125, cacheHits: 14, cacheMisses: 7 };

it("shows material/file progress, elapsed time and reused checks", () => {
  render(<FileCheckProgress progress={progress} resume={null} total={50} />);
  expect(screen.getByRole("status")).toHaveTextContent("12 / 50 materials inspected · Elapsed 2:05");
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "24");
  expect(screen.getByText(progress.active[0].identity)).toBeVisible();
  expect(screen.getByText(progress.active[0].file!)).toBeVisible();
  expect(screen.getByText("Reused file checks: 14 · Fresh file checks: 7")).toBeVisible();
});

it("does not display 100 percent before the server confirms the committed result", () => {
  const { rerender } = render(<FileCheckProgress progress={{ ...progress, completed: 50, active: [] }} resume={null} total={50} />);
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "99");
  expect(screen.getByText(/Verifying and saving results/)).toBeVisible();
  rerender(<FileCheckProgress progress={{ ...progress, completed: 50, status: "COMPLETED", active: [] }} resume={null} total={50} />);
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "100");
});

it("offers one explicit recovery action for the existing check", () => {
  const resume = vi.fn(); render(<FileCheckProgress progress={progress} resume={resume} total={50} />);
  expect(screen.getByRole("alert")).toHaveTextContent("Resume the same check");
  fireEvent.click(screen.getByRole("button", { name: "Resume file check" }));
  expect(resume).toHaveBeenCalledOnce();
});
