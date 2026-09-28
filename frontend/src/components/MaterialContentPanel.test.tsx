import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaterialContentPanel } from "./MaterialContentPanel";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import type { Role } from "../auth/client";
import { materialFromDto } from "../api/materialDto";
import { materialDto, processorDto } from "../test/materialFixtures";
import { metadataClient } from "../api/metadataClient";
import { requestNavigation } from "../navigationGuard";
import { contentFromDto } from "../api/catalogClient";

beforeEach(() => { vi.spyOn(metadataClient, "inspect").mockResolvedValue({ available: false, writesEnabled: false, editable: false, expectedUpdatedAt: materialDto.updated_at, sha256: null, sourceStatus: "MISSING", active: null, values: { hex_color: null, width_cm: null, height_cm: null } }); });
const category = { id: "10000000-0000-4000-8000-000000000001", value: "Stone", version: 1, is_active: true };
const collection = { ...category, id: "10000000-0000-4000-8000-000000000002", value: "Studio", brand_id: materialDto.published_brand_id };
const empty = { material_id: materialDto.id, revision: 0, description: null, credits: null, tags: [], categories: [], collections: [], content_status: "EMPTY" };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

it("hides the previous account's initial content while the new account reloads", async () => {
  const content = { ...empty, revision: 1, description: "First account draft", content_status: "MANUAL_DRAFT" };
  let second = false, resolve!: (value: Response) => void;
  const pending = new Promise<Response>((done) => { resolve = done; });
  vi.stubGlobal("fetch", vi.fn(async (path: string) => {
    if (path.endsWith("/content-history")) return json([{ id: category.id, revision: 1, actor_id: processorDto.id,
      reason: "First account history", created_at: "2026-09-18T08:00:00Z", snapshot: content }]);
    if (path.endsWith("/content")) return second ? pending : json(content);
    return json([]);
  }));
  const tree = (id: string) => <SessionContext.Provider value={{ session: { user: { ...processorDto, id, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialContentPanel material={materialFromDto(materialDto)} onChanged={vi.fn()} />
  </SessionContext.Provider>;
  const view = render(tree(processorDto.id));
  await screen.findByDisplayValue("First account draft");
  expect(screen.queryByText("Content history")).not.toBeInTheDocument();
  second = true; view.rerender(tree(category.id));
  expect(screen.queryByText(/First account history/)).not.toBeInTheDocument();
  expect(screen.queryByDisplayValue("First account draft")).not.toBeInTheDocument();
  await act(async () => resolve(json({ ...content, description: "Second account draft" })));
  await screen.findByDisplayValue("Second account draft");
});

it("accepts AI content only with complete provenance and preserves its declared origin", () => {
  const provenance = { draft_id: category.id, provider: "Synthetic tool", model: "fixture-v1", prompt_version: "pbr-1", context_hash: "a".repeat(64), edited: true };
  expect(() => contentFromDto({ ...empty, content_status: "AI_DRAFT" })).toThrow();
  expect(() => contentFromDto({ ...empty, content_status: "AI_DRAFT", ai_provenance: { ...provenance, context_hash: "bad" } })).toThrow();
  expect(contentFromDto({ ...empty, revision: 1, content_status: "AI_DRAFT", ai_provenance: provenance }).aiProvenance).toMatchObject({ draftId: category.id, edited: true, model: "fixture-v1" });
  expect(contentFromDto({ ...empty, revision: 1, content_status: "APPROVED", ai_provenance: provenance }).status).toBe("APPROVED");
});
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); vi.restoreAllMocks(); });
function setup(role: Role = "ADMIN", inactive = false, required = false) {
  const changed = vi.fn(); const posts: RequestInit[] = [];
  const busy = vi.fn();
  const state = { ...empty, categories: required ? [category] : inactive ? [{ ...category, is_active: false }] : [], ...(required ? { required_category_id: category.id, required_category_code: "G03" } : {}) };
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.endsWith("/online-categories")) return json([{ ...category, is_active: !inactive }]);
    if (path.includes("/collections")) return json([collection]);
    if (path.endsWith("/content-history")) return json([]);
    if (init?.method === "POST") {
      posts.push(init);
      return json({ ...state, revision: 1, content_status: "MANUAL_DRAFT" });
    }
    return json(state);
  });
  vi.stubGlobal("fetch", fetch); setSessionToken("t".repeat(43));
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialContentPanel material={materialFromDto(materialDto)} onChanged={changed} onBusyChange={busy} />
  </SessionContext.Provider>);
  return { changed, posts, fetch, busy };
}
async function fill() {
  await screen.findByRole("button", { name: "Save library data" });
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Synthetic stone" } });
  fireEvent.change(screen.getByLabelText("Credits"), { target: { value: "8" } });
  fireEvent.change(screen.getByLabelText("Tags, one per line"), { target: { value: "matte\nstone" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Stone" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Studio" }));
}
it("saves individual values without a reason, with the content revision, CSRF and an idempotency key", async () => {
  const { posts, changed } = setup(); await fill();
  fireEvent.click(screen.getByRole("button", { name: "Save library data" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(JSON.parse(String(posts[0].body))).toEqual({ idempotency_key: expect.any(String), expected_revision: 0,
    description: "Synthetic stone", credits: 8, tags: ["matte", "stone"], category_ids: [category.id], collection_ids: [collection.id] });
  expect(posts[0].headers).toEqual(expect.objectContaining({ "X-CSRF-Token": "t".repeat(43) }));
});
it("hides the reason and approval requirement while still rejecting colon-joined tags", async () => {
  const { posts } = setup(); await screen.findByRole("button", { name: "Save library data" });
  expect(screen.getByRole("button", { name: "Save library data" })).toBeEnabled();
  expect(screen.queryByLabelText("Reason for content change")).not.toBeInTheDocument();
  expect(screen.queryByText(/requires a new approval|human approval|approval is still required/i)).not.toBeInTheDocument();
  await fill(); fireEvent.change(screen.getByLabelText("Tags, one per line"), { target: { value: "matte:stone" } });
  fireEvent.click(screen.getByRole("button", { name: "Save library data" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("without colons"); expect(posts).toHaveLength(0);
});
it("retains the exact unknown-outcome request and freezes inputs until retry", async () => {
  const { fetch, changed } = setup(); await fill(); fetch.mockRejectedValueOnce(new TypeError("Synthetic timeout"));
  fireEvent.click(screen.getByRole("button", { name: "Save library data" }));
  await screen.findByRole("alert"); expect(screen.getByLabelText("Description")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reload source" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Recover library data save" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  const calls = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(calls).toHaveLength(2); expect(calls[0][1]?.body).toBe(calls[1][1]?.body);
});
it("keeps a rejected draft visible for correction and explicit reload", async () => {
  const { fetch, changed } = setup(); await fill(); fetch.mockResolvedValueOnce(json({ detail: { code: "CONTENT_REVISION_CHANGED" } }, 409));
  fireEvent.click(screen.getByRole("button", { name: "Save library data" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("current revision");
  expect(screen.getByLabelText("Description")).toHaveValue("Synthetic stone"); expect(changed).not.toHaveBeenCalled();
});
it("allows removal of a retired selected value, then prevents adding it back", async () => {
  setup("ADMIN", true); const checkbox = await screen.findByRole("checkbox", { name: /Stone/ });
  expect(checkbox).toBeChecked(); expect(checkbox).toBeEnabled(); fireEvent.click(checkbox); expect(checkbox).toBeDisabled();
});
it("offers leadership read-only content and audit history", async () => {
  setup("LEADERSHIP"); await screen.findByLabelText("Description");
  expect(screen.queryByRole("button", { name: "Save library data" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Description")).toBeDisabled();
});
it.each(["METAL", null])("uses the persisted category abbreviation %s instead of the bundled workbook code", async abbreviation => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => {
    if (path.endsWith("/online-categories")) return json([{ ...category, value: "Metal / Tiles", abbreviation }]);
    if (path.includes("/collections")) return json([]);
    return json(empty);
  }));
  render(<SessionContext.Provider value={{ session: { user: { ...processorDto, role: "ADMIN" }, must_change_password: false, csrf_token: "t".repeat(43) }, pending: false, logout: vi.fn(), changePassword: vi.fn() }}>
    <MaterialContentPanel material={materialFromDto(materialDto)} onChanged={vi.fn()} />
  </SessionContext.Provider>);
  expect(await screen.findByRole("checkbox", { name: abbreviation ? "METAL · Metal / Tiles" : "Metal / Tiles" })).toBeVisible();
  expect(screen.queryByRole("checkbox", { name: "K03 · Metal / Tiles" })).not.toBeInTheDocument();
});
it.each([{ revision: -1 }, { credits: 1.5 }, { tags: "a:b" }, { content_status: "UNKNOWN" }, { categories: [{ ...category, id: "invalid" }] }])(
  "fails closed on a malformed content response %j", (change) => { expect(() => contentFromDto({ ...empty, ...change })).toThrow(); },
);

it("locks the main category and saves content and source values in one durable request without rounding untouched sizes", async () => {
  vi.mocked(metadataClient.inspect).mockResolvedValue({ available: true, writesEnabled: true, editable: true, expectedUpdatedAt: materialDto.updated_at,
    sha256: "a".repeat(64), sourceStatus: "VALID", active: null, values: { hex_color: "#A1B2C3", width_cm: "12.957", height_cm: "13.845" } });
  const save = vi.spyOn(metadataClient, "save").mockResolvedValue({ id: category.id, status: "COMPLETED", failure: null });
  const { changed, posts } = setup("ADMIN", false, true);
  const main = await screen.findByRole("checkbox", { name: "Stone (Main category)" });
  expect(main).toBeChecked(); expect(main).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Color HEX" })).toHaveTextContent("#A1B2C3");
  expect(screen.getByRole("spinbutton", { name: "Sample width (cm)" })).toHaveAttribute("step", "0.1");
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Combined change" } });
  fireEvent.click(screen.getByRole("combobox", { name: "Color HEX" }));
  fireEvent.click(screen.getByRole("option", { name: "#999999" }));
  fireEvent.click(screen.getByRole("button", { name: "Save library data" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(posts).toHaveLength(0);
  expect(save).toHaveBeenCalledWith(materialDto.id, expect.objectContaining({ expected_sha256: "a".repeat(64), values: { hex_color: "#999999", width_cm: "12.957", height_cm: "13.845" },
    content: expect.objectContaining({ description: "Combined change", category_ids: [category.id] }) }));
});

it("requires one decimal place for edited sizes and preserves the exact combined request after a timeout", async () => {
  vi.mocked(metadataClient.inspect).mockResolvedValue({ available: true, writesEnabled: true, editable: true, expectedUpdatedAt: materialDto.updated_at,
    sha256: null, sourceStatus: "MISSING", active: null, values: { hex_color: null, width_cm: null, height_cm: null } });
  const save = vi.spyOn(metadataClient, "save").mockRejectedValueOnce(new TypeError("timeout")).mockResolvedValue({ id: category.id, status: "COMPLETED", failure: null });
  setup(); const width = await screen.findByRole("spinbutton", { name: "Sample width (cm)" });
  fireEvent.change(width, { target: { value: "12.34" } }); fireEvent.click(screen.getByRole("button", { name: "Save library data" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("one decimal"); expect(save).not.toHaveBeenCalled();
  fireEvent.change(width, { target: { value: "12.3" } }); fireEvent.click(screen.getByRole("button", { name: "Save library data" }));
  await screen.findByRole("button", { name: "Recover library data save" }); expect(width).toBeDisabled();
  const original = save.mock.calls[0][1];
  fireEvent.click(screen.getByRole("button", { name: "Recover library data save" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2)); expect(save.mock.calls[1][1]).toBe(original);
});

it("protects an unsaved library draft from property refresh and navigation until explicit save or discard", async () => {
  const { busy, posts } = setup();
  const description = await screen.findByLabelText("Description");
  expect(busy).toHaveBeenLastCalledWith(false);
  fireEvent.change(description, { target: { value: "Unsaved description" } });
  expect(busy).toHaveBeenLastCalledWith(true);
  expect(requestNavigation("/materials")).toBe(false);
  expect(screen.getByRole("button", { name: "Reload source" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save library data" })).toBeEnabled();
  expect(screen.getByText(/Save or discard these changes/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  await waitFor(() => expect(screen.getByLabelText("Description")).toHaveValue(""));
  expect(busy).toHaveBeenLastCalledWith(false);
  expect(requestNavigation("/materials")).toBe(true);
  expect(posts).toHaveLength(0);
});
