import { expect, test } from "@playwright/test";
import { runManifest } from "./run-manifest";
import { retainedPass, signInThroughApi } from "./auth-helpers";
import { notionConfig, notionComparisonDto, notionId } from "../src/test/notionFixtures";

test("Notion comparison stays disabled in deployment; synthetic UI comparison never applies values", async ({ page }) => {
  await signInThroughApi(page);
  const name = "E2E Notion company " + runManifest.runGuid;
  const auth = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": auth.csrf_token };
  if (!retainedPass) {
    const response = await page.request.post("/api/companies", { headers, data: {
      name, country: "CZ", website: "https://example.invalid/", notion_page_id: notionId,
    } });
    expect(response.status()).toBe(201);
  }
  const companies = await (await page.request.get("/api/companies")).json();
  const company = companies.find((item: { name: string }) => item.name === name);
  expect(company).toBeTruthy(); expect(company.notion_page_id).toBe(notionId);
  const path = `/api/companies/${company.id}`;
  const before = await (await page.request.get(path)).json();
  expect((await (await page.request.get("/api/integrations/notion")).json()).enabled).toBe(false);
  const disabled = await page.request.post(path + "/notion-preview", { headers, data: { expected_page_id: notionId } });
  expect(disabled.status()).toBe(503); expect((await disabled.json()).detail.code).toBe("NOTION_DISABLED");
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
});
