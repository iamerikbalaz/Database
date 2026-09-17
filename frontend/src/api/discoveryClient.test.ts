import { afterEach, expect, it, vi } from "vitest";
import { discoveryClient, discoveryFromDto, discoveryPath } from "./discoveryClient";
import { materialDto } from "../test/materialFixtures";

const id = materialDto.id, identity = materialDto.technical_identity;
const payload = () => ({ material_id: id, technical_identity: identity, parent_path: "library", omitted_entries: 1,
  directories: [{ name: identity, path: "library/" + identity, identity_matches: true }] });
afterEach(() => vi.unstubAllGlobals());
it("binds the exact material, identity and requested parent", () => {
  expect(discoveryFromDto(payload(), id, identity, "library").directories[0].identityMatches).toBe(true);
  expect(() => discoveryFromDto(payload(), id, "OTHER", "library")).toThrow();
  expect(() => discoveryFromDto(payload(), id, identity, "other")).toThrow();
});
it.each(["/", "../x", "a//b", "a\\b", "C:/x", "a\u202eb", "a\ud800b", "é".repeat(128), Array(17).fill("a").join("/")])("rejects an unsafe path %s", (path) => expect(() => discoveryPath(path)).toThrow());
it.each(["match", "path", "duplicate", "count", "fraction", "name", "id"])("rejects inconsistent server data %s", (kind) => {
  const value = payload();
  if (kind === "match") value.directories[0].identity_matches = false;
  if (kind === "path") value.directories[0].path = "other/" + identity;
  if (kind === "duplicate") value.directories.push(value.directories[0]);
  if (kind === "count") value.omitted_entries = 4096;
  if (kind === "fraction") value.omitted_entries = 1.1;
  if (kind === "name") value.directories[0].name = "";
  if (kind === "id") value.material_id = "50000000-0000-4000-8000-000000000002";
  expect(() => discoveryFromDto(value, id, identity, "library")).toThrow();
});
it("uses the authenticated POST transport and never sends an invalid path", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload()))); vi.stubGlobal("fetch", fetcher);
  await discoveryClient.listing(id, identity, "library");
  expect(fetcher).toHaveBeenCalledWith(expect.stringContaining(`/materials/${id}/folder-discovery`), expect.objectContaining({ method: "POST", cache: "no-store", credentials: "same-origin", body: JSON.stringify({ parent_path: "library" }) }));
  await expect(discoveryClient.listing(id, identity, "../private")).rejects.toThrow(); expect(fetcher).toHaveBeenCalledOnce();
});
