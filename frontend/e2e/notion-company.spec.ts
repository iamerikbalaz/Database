import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { runManifest } from "./run-manifest";
import { retainedPass, signInThroughApi } from "./auth-helpers";
import { notionConfig, notionComparisonDto, notionId } from "../src/test/notionFixtures";
import { notionAdoptionDto } from "../src/test/notionAdoptionFixtures";
import type { NotionAdoptionRequest } from "../src/api/notionAdoptionClient";

test("Notion deployment stays disabled; synthetic comparison and adoption UI preserve the real company", async ({ page }) => {
  await signInThroughApi(page);
  const name = "E2E Notion company " + runManifest.runGuid;
  const auth = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": auth.csrf_token };
  if (!retainedPass) {
    const response = await page.request.post("/api/companies", { headers, data: {
      name, country: "CZ", website: "https://example.invalid/", notion_page_id: notionId,
    } });
    expect(response.status()).toBe(201);
    const created = await response.json();
    expect((await page.request.patch(`/api/companies/${created.id}`, { headers, data: { legal_name: "Synthetic audited legal name" } })).status()).toBe(200);
  }
  const companies = await (await page.request.get("/api/companies")).json();
  const company = companies.find((item: { name: string }) => item.name === name);
  expect(company).toBeTruthy(); expect(company.notion_page_id).toBe(notionId);
  const path = `/api/companies/${company.id}`;
  const before = await (await page.request.get(path)).json();
  expect((await (await page.request.get("/api/integrations/notion")).json()).enabled).toBe(false);
  const disabled = await page.request.post(path + "/notion-preview", { headers, data: { expected_page_id: notionId } });
  expect(disabled.status()).toBe(503); expect((await disabled.json()).detail.code).toBe("NOTION_DISABLED");
  const localKeys = ["address", "country", "id", "is_active", "legal_name", "name", "notion_page_id", "updated_at", "vat_id", "website"];
  const binding = Object.fromEntries(localKeys.map((key) => [key, key === "updated_at" ? before[key].replace(/Z$/, "+00:00") : before[key]]));
  const canonical = JSON.stringify(binding).split("").map((character) => character.charCodeAt(0) > 127 ? "\\u" + character.charCodeAt(0).toString(16).padStart(4, "0") : character).join("");
  const disabledAdoption = await page.request.post(path + "/notion-adopt", { headers, data: {
    request_key: crypto.randomUUID(), expected_page_id: notionId, expected_local_sha256: createHash("sha256").update(canonical).digest("hex"),
    expected_observation_sha256: "c".repeat(64), selected_fields: ["name"], reason: "Verify deployed disabled adoption gate",
  } });
  expect(disabledAdoption.status()).toBe(503); expect((await disabledAdoption.json()).detail.code).toBe("NOTION_DISABLED");
  await page.goto(`/companies/${company.id}`);
  const panel = page.locator("details").filter({ has: page.getByText("Notion company comparison", { exact: true }) });
  await panel.locator("summary").click();
  await expect(panel.getByText("Notion comparison is disabled on the server.", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Compare with Notion", exact: true })).toBeDisabled();

  // Explicit UI-only synthetic contract. The deployed backend above remains disabled.
  await panel.locator("summary").click();
  await page.route("**/api/integrations/notion", (route) => route.fulfill({ json: notionConfig() }));
  let reads = 0;
  await page.route("**" + path + "/notion-preview", async (route) => {
    expect(route.request().method()).toBe("POST"); expect(route.request().postDataJSON()).toEqual({ expected_page_id: notionId });
    reads++;
    const response = notionComparisonDto(company.id, notionId); response.fields[1].current = name;
    await route.fulfill({ json: response });
  });
  await panel.locator("summary").click();
  await expect(panel.getByRole("button", { name: "Compare with Notion", exact: true })).toBeEnabled();
  expect(reads).toBe(0);
  await panel.getByRole("button", { name: "Compare with Notion", exact: true }).click();
  await expect(panel.getByRole("heading", { name: "Company comparison", exact: true })).toBeVisible();
  await expect(panel.getByText("Synthetic česká company", { exact: true })).toBeVisible();
  await expect(panel.getByText("Empty", { exact: true })).toBeVisible();
  expect(reads).toBe(1);
  await page.setViewportSize({ width: 1280, height: 900 });
  await panel.screenshot({ path: test.info().outputPath("notion-comparison-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await panel.screenshot({ path: test.info().outputPath("notion-comparison-mobile.png") });
  expect(await (await page.request.get(path)).json()).toEqual(before);
  const history = page.locator("details").filter({ has: page.getByText("Company change history", { exact: true }) });
  await history.locator("summary").click();
  await history.getByText(/^Change 2 · Company updated/).click();
  await expect(history.getByText("Synthetic audited legal name", { exact: true })).toBeVisible();
  const events = await (await page.request.get(path + "/history")).json();
  expect(events.items.map((item: { version: number }) => item.version)).toEqual([2, 1]);
  expect(events.items[0].before.legal_name).toBe(null); expect(events.items[0].after.legal_name).toBe("Synthetic audited legal name");
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await history.screenshot({ path: test.info().outputPath("company-history-mobile.png") });
  await page.setViewportSize({ width: 1280, height: 900 });
  await history.screenshot({ path: test.info().outputPath("company-history-desktop.png") });

  // Explicit UI-only success/recovery contract. No synthetic success is written
  // to the deployed company or its real audit history; those are checked below.
  const packets: NotionAdoptionRequest[] = [];
  await page.route("**" + path + "/notion-adopt", async (route) => {
    const body = route.request().postDataJSON() as NotionAdoptionRequest; packets.push(body);
    if (packets.length === 1) { await route.abort("failed"); return; }
    expect(body).toEqual(packets[0]);
    const result = notionAdoptionDto(body, company.id, auth.user.id);
    result.event.before.name = name; result.event.before.legal_name = before.legal_name;
    result.event.after.legal_name = before.legal_name; result.event.version = 3;
    await route.fulfill({ json: result });
  });
  await panel.getByLabel("Adopt Name", { exact: true }).check();
  await panel.getByLabel("Adopt Country (clear local value)", { exact: true }).check();
  await panel.getByLabel("Reason for company change", { exact: true }).fill("Reviewed synthetic UI contract");
  await panel.getByLabel("I reviewed the selected local replacements.", { exact: true }).check();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await panel.screenshot({ path: test.info().outputPath("notion-adoption-mobile.png") });
  await page.setViewportSize({ width: 1280, height: 900 });
  await panel.screenshot({ path: test.info().outputPath("notion-adoption-desktop.png") });
  await panel.getByRole("button", { name: "Adopt selected values", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("outcome could not be verified");
  await expect(panel.getByRole("button", { name: "Compare with Notion", exact: true })).toBeDisabled();
  await panel.getByText("Notion company comparison", { exact: true }).click();
  await panel.getByText("Notion company comparison", { exact: true }).click();
  await expect(panel.getByRole("button", { name: "Retry same adoption", exact: true })).toBeEnabled();
  expect(packets).toHaveLength(1);
  await panel.getByRole("button", { name: "Check saved adoption result", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("No saved result is visible yet");
  await panel.getByRole("button", { name: "Retry same adoption", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Saved company change 3. The profile has been refreshed" })).toBeVisible();
  expect(packets).toHaveLength(2); expect(packets[1]).toEqual(packets[0]);
  expect(await (await page.request.get(path)).json()).toEqual(before);
  expect((await (await page.request.get(path + "/history")).json()).items).toEqual(events.items);
});
