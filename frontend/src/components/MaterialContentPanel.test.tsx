import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MaterialContentPanel } from "./MaterialContentPanel";
import { SessionContext } from "../auth/context";
import { setSessionToken } from "../auth/sessionTransport";
import type { Role } from "../auth/client";
import { materialFromDto } from "../api/materialDto";
import { materialDto, processorDto } from "../test/materialFixtures";
import { contentFromDto } from "../api/catalogClient";

const category = { id: "10000000-0000-4000-8000-000000000001", value: "Stone", version: 1, is_active: true };
const collection = { ...category, id: "10000000-0000-4000-8000-000000000002", value: "Studio", brand_id: materialDto.published_brand_id };
const empty = { material_id: materialDto.id, revision: 0, description: null, credits: null, tags: [], categories: [], collections: [], content_status: "EMPTY" };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

it("hides the previous account's initial content and history while the new account reloads", async () => {
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
  fireEvent.click(await screen.findByRole("button", { name: "Load content history" }));
  await screen.findByText(/First account history/);
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
afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });
function setup(role: Role = "ADMIN", inactive = false) {
  const changed = vi.fn(); const posts: RequestInit[] = [];
  const state = { ...empty, categories: inactive ? [{ ...category, is_active: false }] : [] };
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
    <MaterialContentPanel material={materialFromDto(materialDto)} onChanged={changed} />
  </SessionContext.Provider>);
  return { changed, posts, fetch };
}
async function fill() {
  await screen.findByRole("button", { name: "Save publication draft" });
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Synthetic stone" } });
  fireEvent.change(screen.getByLabelText("Credits"), { target: { value: "8" } });
  fireEvent.change(screen.getByLabelText("Tags, one per line"), { target: { value: "matte\nstone" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Stone" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Studio" }));
  fireEvent.change(screen.getByLabelText("Reason for content change"), { target: { value: "Classify synthetic material" } });
}
it("saves individual values with the content revision, CSRF and an idempotency key", async () => {
  const { posts, changed } = setup(); await fill();
  fireEvent.click(screen.getByRole("button", { name: "Save publication draft" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(JSON.parse(String(posts[0].body))).toEqual({ idempotency_key: expect.any(String), expected_revision: 0,
    description: "Synthetic stone", credits: 8, tags: ["matte", "stone"], category_ids: [category.id], collection_ids: [collection.id], reason: "Classify synthetic material" });
  expect(posts[0].headers).toEqual(expect.objectContaining({ "X-CSRF-Token": "t".repeat(43) }));
});
it("requires a reason and rejects colon-joined tags without an HTTP mutation", async () => {
  const { posts } = setup(); await screen.findByRole("button", { name: "Save publication draft" });
  expect(screen.getByRole("button", { name: "Save publication draft" })).toBeDisabled();
  await fill(); fireEvent.change(screen.getByLabelText("Tags, one per line"), { target: { value: "matte:stone" } });
  fireEvent.click(screen.getByRole("button", { name: "Save publication draft" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("without colons"); expect(posts).toHaveLength(0);
});
it("retains the exact unknown-outcome request and freezes inputs until retry", async () => {
  const { fetch, changed } = setup(); await fill(); fetch.mockRejectedValueOnce(new TypeError("Synthetic timeout"));
  fireEvent.click(screen.getByRole("button", { name: "Save publication draft" }));
  await screen.findByRole("alert"); expect(screen.getByLabelText("Description")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reload content and discard local edits" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry same content save" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  const calls = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(calls).toHaveLength(2); expect(calls[0][1]?.body).toBe(calls[1][1]?.body);
});
it("keeps a rejected draft visible for correction and explicit reload", async () => {
  const { fetch, changed } = setup(); await fill(); fetch.mockResolvedValueOnce(json({ detail: { code: "CONTENT_REVISION_CHANGED" } }, 409));
  fireEvent.click(screen.getByRole("button", { name: "Save publication draft" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("another person changed");
  expect(screen.getByLabelText("Description")).toHaveValue("Synthetic stone"); expect(changed).not.toHaveBeenCalled();
});
it("allows removal of a retired selected value, then prevents adding it back", async () => {
  setup("ADMIN", true); const checkbox = await screen.findByRole("checkbox", { name: /Stone/ });
  expect(checkbox).toBeChecked(); expect(checkbox).toBeEnabled(); fireEvent.click(checkbox); expect(checkbox).toBeDisabled();
});
it("offers leadership read-only content and audit history", async () => {
  setup("LEADERSHIP"); await screen.findByText(/Revision 0/);
  expect(screen.queryByRole("button", { name: "Save publication draft" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Description")).not.toBeInTheDocument();
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
