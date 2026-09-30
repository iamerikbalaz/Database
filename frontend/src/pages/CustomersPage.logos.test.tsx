import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { directoryClient, parseCustomer } from "../api/directoryClient";
import { customerDtos } from "../test/directoryFixtures";
import { CustomersPage } from "./CustomersPage";

afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

it("restores the first logo column while retaining other saved column preferences", async () => {
  const customer = parseCustomer({ ...customerDtos[0], has_logo: true });
  vi.spyOn(directoryClient, "customers").mockResolvedValue([customer]);
  localStorage.setItem("customers.columns.v1", JSON.stringify(["logo", "website"]));
  render(<CustomersPage navigate={vi.fn()} />);
  const logo = await screen.findByRole("img", { name: `${customer.name} logo` });
  expect(logo).toHaveAttribute("src", directoryClient.logoUrl(customer));
  expect(logo).toHaveClass("customer-logo--thumbnail");
  expect(screen.getAllByRole("columnheader")[0]).toHaveTextContent("Logo");
  expect(screen.queryByRole("columnheader", { name: "Website" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("Properties"));
  expect(screen.getByRole("checkbox", { name: "Logo" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Logo" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "Website" }));
  expect(screen.getByRole("columnheader", { name: "Website" })).toBeInTheDocument();
  expect(JSON.parse(localStorage.getItem("customers.columns.v1")!)).not.toContain("logo");
});
