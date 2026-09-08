import { useLayoutEffect } from "react";
import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { RecordForm, type FormDefinition } from "./RecordForm";

it("focuses the first enabled field during layout, before passive effects", () => {
  const observeFocus = vi.fn();
  const definition: FormDefinition = {
    title: "Create record",
    fields: [
      { name: "owner", apiName: "owner", label: "Owner", type: "select", disabled: true },
      { name: "name", apiName: "name", label: "Name", required: true },
    ],
    initial: { owner: "", name: "" },
    cancel: "/",
    save: vi.fn(),
  };
  function Host() {
    useLayoutEffect(() => {
      observeFocus(document.activeElement);
    }, []);
    return <RecordForm definition={definition} navigate={vi.fn()} onSaved={vi.fn()} />;
  }
  render(<Host />);
  expect(observeFocus).toHaveBeenCalledWith(screen.getByLabelText("Name *"));
});
