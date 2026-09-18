import { expect, test } from "@playwright/test";
import { retainedPass, signInThroughApi } from "./auth-helpers";
import { runManifest } from "./run-manifest";

test("ordinary resource histories retain their actor and before/after values after restart", async ({ page }) => {
  await signInThroughApi(page);
  const auth = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": auth.csrf_token };
  const suffix = runManifest.runGuid.replaceAll("-", ""), name = "E2E audit " + suffix;
  async function create(segment: string, data: Record<string, unknown>) {
    const response = await page.request.post(`/api/${segment}`, { headers, data });
    expect(response.status()).toBe(201); return response.json();
  }
  if (!retainedPass) {
    const brand = await create("brands", { company_id: runManifest.state.companyId, name, folder_prefix: "AUDIT", brand_identifier: "audit_" + suffix });
    const project = await create("projects", { company_id: runManifest.state.companyId, name, project_number: "AUDIT-" + suffix });
    const user = await create("internal-users", { display_name: name, email: `audit-${suffix}@example.invalid`, role: "PROCESSOR" });
    const material = await create("materials", { project_id: project.id, published_brand_id: brand.id, assigned_processor_id: user.id,
      material_name: name, main_category_code: "G03" });
    for (const [segment, id, data] of [
      ["brands", brand.id, { name: name + " revised" }], ["projects", project.id, { notes: "Synthetic audit project note" }],
      ["internal-users", user.id, { display_name: name + " revised" }], ["materials", material.id, { material_name: name + " revised" }],
    ] as const) expect((await page.request.patch(`/api/${segment}/${id}`, { headers, data })).status()).toBe(200);
  }
  const brands = await (await page.request.get("/api/brands")).json();
  const brand = brands.find((item: { brand_identifier: string }) => item.brand_identifier === "audit_" + suffix);
  const projects = await (await page.request.get("/api/projects")).json();
  const project = projects.find((item: { project_number: string }) => item.project_number === "AUDIT-" + suffix);
  const users = await (await page.request.get("/api/internal-users")).json();
  const user = users.find((item: { email: string }) => item.email === `audit-${suffix}@example.invalid`);
  expect(brand).toBeTruthy(); expect(project).toBeTruthy(); expect(user).toBeTruthy();
  const materials = await (await page.request.get(`/api/materials?project_id=${project.id}`)).json();
  const material = materials.find((item: { published_brand_id: string }) => item.published_brand_id === brand.id);
  expect(material).toBeTruthy();
  for (const [kind, segment, id, path, label, expected] of [
    ["BRAND", "brands", brand.id, `/brands/${brand.id}`, "Brand change history", name + " revised"],
    ["PROJECT", "projects", project.id, `/projects/${project.id}`, "Project change history", "Synthetic audit project note"],
    ["USER", "internal-users", user.id, "/settings/users", "Account profile change history", name + " revised"],
    ["MATERIAL", "materials", material.id, `/materials/${material.id}`, "Material record change history", name + " revised"],
  ] as const) {
    const response = await page.request.get(`/api/${segment}/${id}/history`); expect(response.status()).toBe(200);
    const history = await response.json(); expect(history.resource_kind).toBe(kind); expect(history.resource_id).toBe(id);
    expect(history.items.map((item: { version: number }) => item.version)).toEqual([2, 1]);
    expect(history.items[0].before).toEqual(history.items[1].after);
    expect(history.items[1].before).toEqual({}); expect(history.items[0].actor_id).toBe(auth.user.id);
    await page.goto(path);
    const scope = kind === "USER" ? page.locator(".account-list > div").filter({ has: page.getByRole("form", { name: `Manage ${name} revised`, exact: true }) }) : page.locator("main");
    const panel = scope.locator("details").filter({ has: page.getByText(label, { exact: true }) });
    await panel.locator(":scope > summary").click();
    await panel.getByText(/^Change 2 · Record updated/).click();
    const entry = panel.locator("details").filter({ has: page.getByText(/^Change 2 · Record updated/) });
    await expect(entry.getByText(expected, { exact: true })).toBeVisible();
    await expect(entry.getByText(`Actor: ${auth.user.id}`, { exact: true })).toBeVisible();
    if (kind !== "PROJECT") await expect(entry.getByText(name, { exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await panel.screenshot({ path: test.info().outputPath(`resource-history-${kind.toLowerCase()}-mobile.png`) });
    await page.setViewportSize({ width: 1280, height: 900 });
    await panel.screenshot({ path: test.info().outputPath(`resource-history-${kind.toLowerCase()}-desktop.png`) });
  }
});
