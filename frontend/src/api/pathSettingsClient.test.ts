import { afterEach, expect, it, vi } from "vitest";
import { pathSettingsClient } from "./pathSettingsClient";
import { setSessionToken } from "../auth/sessionTransport";

afterEach(() => { vi.unstubAllGlobals(); setSessionToken(null); });

it.each([null, "C:\\České složky"])("selects a folder with CSRF using only the field name (%s)", async folder => {
  setSessionToken("s".repeat(43));
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ folder_path: folder }), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  expect(await pathSettingsClient.selectFolder("published_library_root")).toBe(folder);
  expect(fetch).toHaveBeenCalledExactlyOnceWith("/api/settings/paths/select-folder", expect.objectContaining({
    method: "POST", credentials: "same-origin", cache: "no-store", body: JSON.stringify({ field: "published_library_root" }),
    headers: expect.objectContaining({ "X-CSRF-Token": "s".repeat(43) }),
  }));
});

it("parses the saved future library path and desktop capability", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ version: 4, sbs_templates_root: "C:/Templates", orders_root: "R:/Orders", materials_root: "C:/Test_data", published_library_root: "Z:/Published", can_select_folder: false }), { status: 200 })));
  expect(await pathSettingsClient.current()).toEqual({ version: 4, sbsTemplatesRoot: "C:/Templates", ordersRoot: "R:/Orders", materialsRoot: "C:/Test_data", publishedLibraryRoot: "Z:/Published", canSelectFolder: false });
});
