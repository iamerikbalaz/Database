import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { materialFromDto } from "../api/materialDto";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import { requestNavigation } from "../navigationGuard";
import { materialDto, processorDto } from "../test/materialFixtures";
import { MaterialMetadataEditor } from "./MaterialMetadataEditor";

const id = "10000000-0000-4000-8000-000000000001";
const observed = { available: true, writes_enabled: true, editable: true, expected_updated_at: materialDto.updated_at,
  sha256: null, source_status: "MISSING", values: { hex_color: null, width_cm: "12", height_cm: "34" }, active_operation: null };
const completed = { id, status: "COMPLETED", result: { failure_code: null, sha256: "a".repeat(64) } };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });

function setup(patch: Partial<typeof observed> = {}) {
  const calls: { path: string; init?: RequestInit }[] = [];
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    return json(init?.method === "POST" ? completed : { ...observed, ...patch });
  });
  vi.stubGlobal("fetch", fetch); setSessionToken("t".repeat(43));
  const onChanged = vi.fn(async () => true);
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialMetadataEditor material={materialFromDto({ ...materialDto, folder_path: "library/" + materialDto.technical_identity })} onChanged={onChanged} />
  </SessionContext.Provider>);
  return { calls, fetch, onChanged };
}

it("shows disabled cached values when the live source is unavailable", async () => {
  setup({ available: false, writes_enabled: false, source_status: "UNAVAILABLE" });
  expect(await screen.findByText("Source unavailable. Showing the last recorded values.")).toBeVisible();
  expect(screen.getByLabelText("Sample width (cm)")).toHaveValue("12");
  expect(screen.getByRole("button", { name: "Save metadata" })).toBeDisabled();
});

it("honors the explicit source write gate", async () => {
  setup({ writes_enabled: false });
  expect(await screen.findByText("Read-only: source writes are disabled.")).toBeVisible();
  expect(screen.getByLabelText("Color HEX")).toBeDisabled();
});

it("saves the three exact values with the observed missing-file proof", async () => {
  const { calls, onChanged } = setup();
  await screen.findByLabelText("Color HEX");
  fireEvent.change(screen.getByLabelText("Color HEX"), { target: { value: "#aabbcc" } });
  fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
  const payload = JSON.parse(calls.find((item) => item.init?.method === "POST")!.init!.body as string);
  expect(payload.expected_sha256).toBeNull();
  expect(payload.values).toEqual({ hex_color: "#aabbcc", width_cm: "12", height_cm: "34" });
});

it("rejects invalid color and precision before sending", async () => {
  const { calls } = setup();
  await screen.findByLabelText("Color HEX");
  fireEvent.change(screen.getByLabelText("Sample width (cm)"), { target: { value: "0.00001" } });
  fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("four decimal places");
  expect(calls.every((item) => item.init?.method === "GET")).toBe(true);
});

it("blocks leaving and retries the exact request after an uncertain response", async () => {
  const { calls, fetch } = setup();
  await screen.findByLabelText("Color HEX");
  fetch.mockImplementationOnce(async (path, init) => { calls.push({ path, init }); throw new TypeError("lost response"); });
  fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));
  await screen.findByRole("button", { name: "Recover metadata save" });
  expect(requestNavigation("/materials")).toBe(false);
  expect(screen.getByLabelText("Sample width (cm)")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Recover metadata save" }));
  await waitFor(() => expect(calls.filter((item) => item.init?.method === "POST")).toHaveLength(2));
  const posts = calls.filter((item) => item.init?.method === "POST");
  expect(posts[0].init!.body).toEqual(posts[1].init!.body);
  await waitFor(() => expect(requestNavigation("/materials")).toBe(true));
});

it("recovers a durable active operation using its operation id", async () => {
  const { calls } = setup({ active_operation: { id, status: "RUNNING", result: null } as never });
  fireEvent.click(await screen.findByRole("button", { name: "Recover metadata save" }));
  await waitFor(() => expect(calls.some((item) => item.path.endsWith(`/source-metadata/${id}/resume`))).toBe(true));
});

it("does not create an uncertain recovery state for a known unavailable source", async () => {
  const { fetch } = setup();
  await screen.findByLabelText("Color HEX");
  fetch.mockResolvedValueOnce(json({ detail: { code: "SOURCE_METADATA_UNAVAILABLE" } }, 503));
  fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("No save was started");
  expect(screen.queryByRole("button", { name: "Recover metadata save" })).not.toBeInTheDocument();
  expect(requestNavigation("/materials")).toBe(true);
});
