import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the connected backend state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          status: "ok",
          database: "connected",
          service: "backend",
          version: "0.1.0",
        }),
      }),
    );

    render(<App />);

    expect(screen.getByText("Ověřuji spojení…")).toBeInTheDocument();
    expect(await screen.findByText("Připojeno")).toBeInTheDocument();
    expect(screen.getByText(/Databáze: připojena/)).toBeInTheDocument();
  });

  it("shows a retry action when the backend cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<App />);

    expect(await screen.findByText("Nedostupné")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zkusit znovu" })).toBeInTheDocument();
  });
});
