import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { AccountsPage } from "./AccountsPage";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import { processorDto } from "../test/materialFixtures";
import { pendingRecordCommands } from "../forms/pendingRecordCommands";
import { RecordForm, type FormDefinition } from "../forms/RecordForm";

const owners: string[] = [];
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const created = { ...processorDto, id: "f0000000-0000-4000-8000-000000000001", display_name: "New Example", email: "new@example.invalid" };
function actor() { const id = crypto.randomUUID(); owners.push(id); return id; }
function tree(id: string, children: ReactNode) {
  setSessionToken("t".repeat(43));
  return <SessionContext.Provider value={{ session: { user: { ...processorDto, id, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) },
    pending: false, logout: vi.fn(), changePassword: vi.fn() }}>{children}</SessionContext.Provider>;
}
function page(id: string, navigate = vi.fn()) { return tree(id, <AccountsPage navigate={navigate} />); }
function create() {
  const form = screen.getByRole("form", { name: "Create account" });
  fireEvent.change(within(form).getByLabelText("Display name"), { target: { value: created.display_name } });
  fireEvent.change(within(form).getByLabelText("Email"), { target: { value: created.email } });
  fireEvent.submit(form); return form;
}
afterEach(() => { for (const id of owners.splice(0)) pendingRecordCommands.set(id, null); vi.unstubAllGlobals(); vi.restoreAllMocks(); setSessionToken(null); });

it("retains an uncertain profile across navigation, blocks other ordinary forms and retries the exact key and fields", async () => {
  const id = actor(), writes: RequestInit[] = [], navigate = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      writes.push(init); if (writes.length === 1) throw new Error("Connection lost"); return json(created, 201);
    }
    return json([processorDto]);
  }));
  const view = render(page(id)); create(); await screen.findByRole("button", { name: "Retry exact save" });
  expect(within(screen.getByRole("form", { name: "Create account" })).getByLabelText("Email")).toBeDisabled();
  expect(screen.getAllByRole("region", { name: "Pending save" })).toHaveLength(1);
  const other: FormDefinition = { title: "Another ordinary form", fields: [], initial: {}, cancel: "/companies", save: vi.fn(),
    command: { kind: "COMPANY", action: "CREATED", targetId: null, editorPath: "/companies/new", payload: () => ({ name: "Example" }) } };
  view.rerender(tree(id, <RecordForm definition={other} navigate={navigate} onSaved={vi.fn()} />));
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Open pending form" })); expect(navigate).toHaveBeenCalledWith("/settings/users");
  view.rerender(page(id));
  const form = screen.getByRole("form", { name: "Create account" });
  expect(within(form).getByLabelText("Email")).toHaveValue(created.email);
  fireEvent.change(within(form).getByLabelText("Email"), { target: { value: "different@example.invalid" } });
  fireEvent.click(within(form).getByRole("button", { name: "Retry exact save" }));
  await screen.findByText(/Profile created\. Use Set or reset access/);
  expect(writes).toHaveLength(2);
  expect(writes[1].body).toBe(writes[0].body);
  expect(new Headers(writes[1].headers).get("Idempotency-Key")).toBe(new Headers(writes[0].headers).get("Idempotency-Key"));
  expect(JSON.parse(String(writes[0].body))).toEqual({ display_name: created.display_name, email: created.email, role: "PROCESSOR" });
  expect(pendingRecordCommands.get(id)).toBeNull();
});

it("explicitly reads a profile receipt without another create and refreshes current account data", async () => {
  const id = actor(); let sent = 0, reads = 0;
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") { sent += 1; throw new Error("Connection lost after commit"); }
    if (path.startsWith("/api/resource-commands/")) {
      reads += 1; const packet = pendingRecordCommands.get(id)!;
      return json({ id: crypto.randomUUID(), actor_id: id, request_key: packet.key, kind: "USER", action: "CREATED", resource_id: created.id,
        request_hash: packet.requestHash, response_hash: "b".repeat(64), response: created, created_at: "2026-09-19T00:00:00Z" });
    }
    return json(sent ? [processorDto, created] : [processorDto]);
  }));
  render(page(id)); create(); await screen.findByRole("button", { name: "Check saved result" }); expect(reads).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: "Check saved result" }));
  await screen.findByRole("form", { name: "Manage New Example" });
  expect(sent).toBe(1); expect(reads).toBe(1); expect(pendingRecordCommands.get(id)).toBeNull();
});

it("binds uncertain role changes to one row and shows a later administrator edit after replay", async () => {
  const id = actor(), writes: RequestInit[] = [];
  const other = { ...processorDto, id: created.id, display_name: "Other Example" };
  let current = { ...processorDto };
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "PATCH") {
      writes.push(init); if (writes.length === 1) throw new Error("Connection lost");
      return json({ ...processorDto, role: "PRODUCTION_LEAD", is_active: false });
    }
    return json([current, other]);
  }));
  const view = render(page(id));
  const form = await screen.findByRole("form", { name: `Manage ${processorDto.display_name}` });
  fireEvent.change(within(form).getByLabelText("Role"), { target: { value: "PRODUCTION_LEAD" } });
  fireEvent.click(within(form).getByLabelText("Active")); fireEvent.submit(form);
  await screen.findByRole("button", { name: "Retry exact save" });
  expect(within(screen.getByRole("form", { name: "Manage Other Example" })).queryByRole("region", { name: "Pending save" })).not.toBeInTheDocument();
  expect(screen.getAllByRole("region", { name: "Pending save" })).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Create profile" })).toBeDisabled();
  expect(screen.getByRole("link", { name: "Open pending profile save" })).toHaveAttribute("href", `#account-save-${processorDto.id}`);
  view.unmount();
  current = { ...current, role: "LEADERSHIP", is_active: true, updated_at: "2026-09-19T00:00:02Z" };
  render(page(id)); const restored = await screen.findByRole("form", { name: `Manage ${processorDto.display_name}` });
  expect(within(restored).getByLabelText("Role")).toHaveValue("PRODUCTION_LEAD");
  expect(within(restored).getByLabelText("Active")).not.toBeChecked();
  fireEvent.click(within(restored).getByRole("button", { name: "Retry exact save" }));
  await waitFor(() => expect(within(screen.getByRole("form", { name: `Manage ${processorDto.display_name}` })).getByLabelText("Role")).toHaveValue("LEADERSHIP"));
  expect(writes).toHaveLength(2); expect(writes[1].body).toBe(writes[0].body);
  expect(new Headers(writes[1].headers).get("Idempotency-Key")).toBe(new Headers(writes[0].headers).get("Idempotency-Key"));
  expect(JSON.parse(String(writes[1].body))).toEqual({ role: "PRODUCTION_LEAD", is_active: false });
});

it("does not attach a late create to another account and recovers the original account's saved result", async () => {
  const first = actor(), second = actor(); let complete!: (value: Response) => void; let listReads = 0;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") return new Promise<Response>((resolve) => { complete = resolve; });
    listReads += 1; return json([processorDto]);
  });
  vi.stubGlobal("fetch", fetch); const view = render(page(first)); create();
  await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1));
  view.rerender(page(second)); await screen.findByRole("form", { name: `Manage ${processorDto.display_name}` });
  const before = listReads; await act(async () => complete(json(created, 201)));
  expect(listReads).toBe(before); expect(screen.queryByText(/Profile created\./)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create profile" })).toBeEnabled();
  view.rerender(page(first));
  fireEvent.click(screen.getByRole("button", { name: "Open saved record" }));
  await screen.findByText(/Profile created\./); expect(pendingRecordCommands.get(first)).toBeNull();
});

it("retires an initial account read when the actor changes", async () => {
  const first = actor(), second = actor(); let complete!: (value: Response) => void; let requests = 0;
  vi.stubGlobal("fetch", vi.fn(async () => {
    requests += 1; return requests === 1 ? new Promise<Response>((resolve) => { complete = resolve; }) : json([created]);
  }));
  const view = render(page(first)); await waitFor(() => expect(requests).toBe(1));
  view.rerender(page(second)); await screen.findByRole("form", { name: "Manage New Example" });
  await act(async () => complete(json([processorDto])));
  expect(screen.queryByRole("form", { name: `Manage ${processorDto.display_name}` })).not.toBeInTheDocument();
  expect(screen.getByRole("form", { name: "Manage New Example" })).toBeInTheDocument();
});

it("links to an existing catalog command and blocks all profile mutations", async () => {
  const id = actor(), navigate = vi.fn();
  pendingRecordCommands.set(id, { key: crypto.randomUUID(), requestHash: "a".repeat(64), scope: { kind: "COMPANY", action: "CREATED", targetId: null, editorPath: "/companies/new" },
    values: { name: "Example" }, save: vi.fn(), phase: "UNKNOWN", wasUnknown: true });
  const fetch = vi.fn(async () => json([processorDto])); vi.stubGlobal("fetch", fetch);
  render(page(id, navigate)); await screen.findByRole("form", { name: `Manage ${processorDto.display_name}` });
  expect(screen.getByRole("button", { name: "Create profile" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save role and status" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Set or reset access" })).toBeDisabled();
  fireEvent.click(screen.getByRole("link", { name: "Open pending form" })); expect(navigate).toHaveBeenCalledWith("/companies/new");
  expect(fetch).toHaveBeenCalledOnce();
});
