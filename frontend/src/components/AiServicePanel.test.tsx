import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AiServicePanel } from "./AiServicePanel";
import { aiServiceClient } from "../api/aiServiceClient";
import { SessionContext } from "../auth/context";
import type { Role } from "../auth/client";
import { materialDto, processorDto } from "../test/materialFixtures";

const id = materialDto.id, credentialId = "10000000-0000-4000-8000-000000000001";
const credential = { id: credentialId, materialId: id, actorId: processorDto.id, createdAt: "2026-09-17T12:00:00Z", expiresAt: "2026-09-17T12:15:00Z", revokedAt: null };
// Synthetic, never-issued fixture. Actual secrets must not appear in UI artifacts.
const token = `reawote_ai_${credentialId}.${"a".repeat(43)}`;
afterEach(() => vi.restoreAllMocks());
function setup(role: Role = "ADMIN") {
  const issue = vi.spyOn(aiServiceClient, "issue").mockResolvedValue({ credential, token });
  const revoke = vi.spyOn(aiServiceClient, "revoke").mockResolvedValue({ ...credential, revokedAt: "2026-09-17T12:03:00Z" });
  const history = vi.spyOn(aiServiceClient, "history").mockResolvedValue({ items: [credential], nextCursor: null });
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <AiServicePanel materialId={id} />
  </SessionContext.Provider>);
  return { issue, revoke, history };
}
function open() { fireEvent.click(screen.getByText("Manage AI service access", { exact: true })); }
function reason() { fireEvent.change(screen.getByLabelText("Reason for service access change"), { target: { value: "Synthetic service authorization" } }); }
it("loads only on request and requires an explicit reason for bounded issuance", async () => {
  const { issue, history } = setup(); expect(history).not.toHaveBeenCalled(); open();
  expect(screen.getByRole("button", { name: "Issue one-time service access" })).toBeDisabled(); reason();
  fireEvent.click(screen.getByRole("button", { name: "Issue one-time service access" }));
  const field = await screen.findByLabelText("One-time service credential"); expect(field).toHaveAttribute("type", "password"); expect(field).toHaveValue(token);
  expect(issue).toHaveBeenCalledWith(id, { idempotency_key: expect.any(String), lifetime_seconds: 900, reason: "Synthetic service authorization" });
  expect(screen.getByRole("button", { name: "Issue one-time service access" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Hide one-time credential and clear this result" }));
  expect(screen.queryByLabelText("One-time service credential")).not.toBeInTheDocument();
});
it("retains the exact unknown issuance request and explains a replay without its secret", async () => {
  const { issue } = setup(); issue.mockRejectedValueOnce(new TypeError("Synthetic timeout")).mockResolvedValueOnce({ credential, token: null });
  open(); reason(); fireEvent.click(screen.getByRole("button", { name: "Issue one-time service access" }));
  await screen.findByText(/The outcome is unknown/); expect(screen.getByLabelText("Reason for service access change")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Load service access history" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry same service access request" }));
  await screen.findByText(/Its secret cannot be recovered/); expect(issue.mock.calls[0]).toEqual(issue.mock.calls[1]);
  expect(screen.queryByLabelText("One-time service credential")).not.toBeInTheDocument();
});
it("handles unavailable clipboard access without exposing the credential in an error", async () => {
  setup(); open(); reason(); fireEvent.click(screen.getByRole("button", { name: "Issue one-time service access" }));
  await screen.findByLabelText("One-time service credential"); fireEvent.click(screen.getByRole("button", { name: "Copy one-time credential" }));
  const error = await screen.findByRole("alert"); expect(error).toHaveTextContent("Clipboard access was unavailable"); expect(error).not.toHaveTextContent(token);
});
it("revokes the issued credential and removes its secret without discarding provenance", async () => {
  const { revoke } = setup(); open(); reason(); fireEvent.click(screen.getByRole("button", { name: "Issue one-time service access" }));
  await screen.findByLabelText("One-time service credential"); reason();
  fireEvent.click(screen.getByRole("button", { name: `Revoke credential ${credentialId}` })); await screen.findByText(/Service access revoked/);
  expect(revoke).toHaveBeenCalledWith(id, credentialId, { idempotency_key: expect.any(String), reason: "Synthetic service authorization" });
  expect(screen.queryByLabelText("One-time service credential")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: `Revoke credential ${credentialId}` })).toBeDisabled();
});
it("retains an unknown revocation for an exact retry", async () => {
  const { revoke } = setup(); revoke.mockRejectedValueOnce(new TypeError("Synthetic timeout")); open();
  fireEvent.click(screen.getByRole("button", { name: "Load service access history" })); await screen.findByText(credentialId); reason();
  fireEvent.click(screen.getByRole("button", { name: `Revoke credential ${credentialId}` })); await screen.findByText(/The outcome is unknown/);
  fireEvent.click(screen.getByRole("button", { name: "Retry same service access request" })); await screen.findByText(/Service access revoked/);
  expect(revoke.mock.calls[0]).toEqual(revoke.mock.calls[1]);
});
it("loads bounded older metadata without a secret", async () => {
  const { history } = setup(); history.mockResolvedValueOnce({ items: [credential], nextCursor: credentialId }); open();
  fireEvent.click(screen.getByRole("button", { name: "Load service access history" })); await screen.findByRole("button", { name: "Older service credentials" });
  fireEvent.click(screen.getByRole("button", { name: "Older service credentials" })); await waitFor(() => expect(history).toHaveBeenLastCalledWith(id, credentialId));
  expect(screen.queryByLabelText("One-time service credential")).not.toBeInTheDocument();
});
it.each(["PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP"] as Role[])("does not mount the secret manager for %s", (role) => {
  const { issue, history } = setup(role); expect(screen.queryByText("Manage AI service access")).not.toBeInTheDocument(); expect(issue).not.toHaveBeenCalled(); expect(history).not.toHaveBeenCalled();
});
