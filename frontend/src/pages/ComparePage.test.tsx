import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ComparePage } from "./ComparePage";
import { httpApiClient } from "../api/client";
import { materialFromDto } from "../api/materialDto";
import { materialDto } from "../test/materialFixtures";

const left = materialFromDto(materialDto);
const right = { ...left, id: "50000000-0000-4000-8000-000000000002", materialName: "Other surface", technicalIdentity: "TEST_0001_G04" };
afterEach(() => vi.restoreAllMocks());
it("compares only returned materials, excludes the other selection and opens its detail", async () => {
  vi.spyOn(httpApiClient, "getMaterials").mockResolvedValue([left, right]); const navigate = vi.fn();
  render(<ComparePage client={httpApiClient} navigate={navigate} />);
  fireEvent.change(await screen.findByRole("combobox", { name: "Left material" }), { target: { value: left.id } });
  expect(within(screen.getByLabelText("Right material", { selector: "select" })).queryByRole("option", { name: /Crystal surface/ })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Right material", { selector: "select" }), { target: { value: right.id } });
  expect(screen.getByRole("region", { name: "Left material" })).toHaveTextContent(left.technicalIdentity);
  expect(screen.getByRole("region", { name: "Right material" })).toHaveTextContent(right.technicalIdentity);
  fireEvent.click(screen.getByRole("button", { name: "Open right material" })); expect(navigate).toHaveBeenCalledWith(`/materials/${right.id}`);
});
it("drops a selected material when a reload no longer returns access to it", async () => {
  vi.spyOn(httpApiClient, "getMaterials").mockResolvedValueOnce([left, right]).mockResolvedValue([right]);
  render(<ComparePage client={httpApiClient} navigate={vi.fn()} />);
  fireEvent.change(await screen.findByRole("combobox", { name: "Left material" }), { target: { value: left.id } });
  fireEvent.click(screen.getByRole("button", { name: "Reload comparison materials" }));
  expect(await screen.findByRole("combobox", { name: "Left material" })).toHaveValue(""); expect(screen.queryByRole("heading", { name: left.materialName })).not.toBeInTheDocument();
});
it("shows empty and failed lists honestly, without invented materials", async () => {
  vi.spyOn(httpApiClient, "getMaterials").mockRejectedValueOnce(new Error("PRIVATE_SYNTHETIC_MARKER")).mockResolvedValue([]);
  render(<ComparePage client={httpApiClient} navigate={vi.fn()} />);
  expect(await screen.findByRole("alert")).not.toHaveTextContent("PRIVATE_SYNTHETIC_MARKER");
  fireEvent.click(screen.getByRole("button", { name: "Try again" })); await screen.findByText("No materials available");
});
