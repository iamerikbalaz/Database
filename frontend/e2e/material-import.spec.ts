import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { retainedPass, signInThroughApi } from "./auth-helpers";
import { e2eOutputDirectory, runManifest } from "./run-manifest";

async function mapAndPreview(page: Page, brandId: string, processorId: string) {
  for (const [label, header] of [["Technical identity", "Identity"], ["Material name", "Name"], ["Project label", "Project"], ["Brand label", "Brand"], ["Processor label", "Processor"]])
    await page.getByRole("combobox", { name: label + " column", exact: true }).selectOption(header);
  await page.getByRole("button", { name: "Load source labels", exact: true }).click();
  await page.getByRole("combobox", { name: "Project: Legacy project", exact: true }).selectOption(runManifest.state.projectId);
  await page.getByRole("combobox", { name: "Brand: Legacy brand", exact: true }).selectOption(brandId);
  await page.getByRole("combobox", { name: "Processor: Legacy processor", exact: true }).selectOption(processorId);
  await page.getByRole("button", { name: "Prepare import preview", exact: true }).click();
  await expect(page.getByRole("article", { name: "Import preview", exact: true })).toContainText("Ready for your confirmation");
}

test("historical CSV and XLSX import preserves identities and immutable history after restart", async ({ page }) => {
  await signInThroughApi(page);
  const failures: string[] = [];
  page.on("pageerror", () => failures.push("browser error"));
  page.on("console", (message) => { if (["error", "warning"].includes(message.type())) failures.push("console diagnostic"); });
  page.on("requestfailed", () => failures.push("request failed"));
  page.on("response", (response) => { if (response.status() >= 400) failures.push(`HTTP ${response.status()}: ${new URL(response.url()).pathname}`); });
  const session = await (await page.request.get("/api/auth/session")).json();
  const headers = { Origin: runManifest.frontendUrl, "X-CSRF-Token": session.csrf_token };
  const processor = (await (await page.request.get(`/api/materials/${runManifest.state.valid.id}`)).json()).assigned_processor_id;
  let brandId: string;
  if (!retainedPass) {
    const company = await page.request.post("/api/companies", { headers, data: { name: "E2E Import Brand Company" } });
    expect(company.status()).toBe(201);
    const brand = await page.request.post("/api/brands", { headers, data: { company_id: (await company.json()).id,
      name: "E2E Historical Brand", folder_prefix: "E2EIMPORT", brand_identifier: "e2e-historical-import" } });
    expect(brand.status()).toBe(201); brandId = (await brand.json()).id;
    await page.goto("/imports");
    const csv = Buffer.from('\ufeffIdentity;Name;Project;Brand;Processor;Legacy state\nE2EIMPORT_0007_G03;"Historical; oak";Legacy project;Legacy brand;Legacy processor;Unverified\n');
    await page.getByLabel("Historical source file").setInputFiles({ name: "historical.csv", mimeType: "text/csv", buffer: csv });
    await page.getByRole("combobox", { name: "CSV delimiter", exact: true }).selectOption(";");
    await page.getByRole("button", { name: "Inspect source", exact: true }).click();
    await mapAndPreview(page, brandId, processor);
    const preview = page.getByRole("article", { name: "Import preview", exact: true });
    await expect(preview).toContainText("Unused columns will not be imported: Legacy state");
    await expect(preview).toContainText("E2E Import Brand Company");
    await expect(preview.getByRole("button", { name: "Confirm import of 1 materials", exact: true })).toBeDisabled();
    await preview.getByLabel("Reason for historical import").fill("Review synthetic CSV migration");
    await preview.getByRole("checkbox").check();
    // Keep first-pass visual evidence under the validated per-run artifact root;
    // Playwright clears its own output directory before the retained-data pass.
    const firstPassImages = path.join(path.dirname(e2eOutputDirectory), "import-first-pass");
    await preview.screenshot({ path: path.join(firstPassImages, "historical-import-preview.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await preview.screenshot({ path: path.join(firstPassImages, "historical-import-preview-mobile.png") });
    await page.setViewportSize({ width: 1280, height: 720 });
    await preview.getByRole("button", { name: "Confirm import of 1 materials", exact: true }).click();
    await expect(page.getByRole("article", { name: "Completed import" })).toContainText("1 materials imported.");
    await page.getByRole("button", { name: "Start another import", exact: true }).click();
    await page.getByLabel("Historical source file").setInputFiles(fileURLToPath(new URL("./fixtures/historical-materials.xlsx", import.meta.url)));
    await page.getByRole("combobox", { name: "Source format", exact: true }).selectOption("XLSX");
    await page.getByRole("button", { name: "Inspect source", exact: true }).click();
    await expect(page.getByRole("button", { name: "Inspect source", exact: true })).toBeDisabled();
    await page.getByRole("combobox", { name: "Worksheet", exact: true }).selectOption("Materials");
    await page.getByRole("button", { name: "Inspect source", exact: true }).click();
    await mapAndPreview(page, brandId, processor);
    await preview.getByLabel("Reason for historical import").fill("Review synthetic XLSX migration");
    await preview.getByRole("checkbox").check();
    await preview.getByRole("button", { name: "Confirm import of 2 materials", exact: true }).click();
    await expect(page.getByRole("article", { name: "Completed import" })).toContainText("2 materials imported.");
  } else {
    const brands = await (await page.request.get("/api/brands")).json();
    brandId = brands.find((item: { brand_identifier: string }) => item.brand_identifier === "e2e-historical-import").id;
  }
  await page.goto("/imports");
  const history = page.locator(".import-history");
  await expect(history.getByRole("button", { name: /^View CSV import/ })).toBeVisible();
  await expect(history.getByRole("button", { name: /^View XLSX import/ })).toBeVisible();
  await history.getByRole("button", { name: /^View XLSX import/ }).click();
  const details = page.getByRole("region", { name: "Import batch details" });
  await expect(details.getByRole("link", { name: "E2EIMPORT_0008_G03", exact: true })).toBeVisible();
  await expect(details.getByRole("link", { name: "E2EIMPORT_0009_G03", exact: true })).toBeVisible();
  await details.screenshot({ path: test.info().outputPath("historical-import-history.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await details.screenshot({ path: test.info().outputPath("historical-import-history-mobile.png") });
  await page.setViewportSize({ width: 1280, height: 720 });
  const batches = await (await page.request.get("/api/material-imports")).json();
  expect(batches.items).toHaveLength(2);
  expect(batches.items.map((item: { row_count: number }) => item.row_count).sort()).toEqual([1, 2]);
  const materials = await (await page.request.get(`/api/materials?published_brand_id=${brandId}`)).json();
  expect(materials).toHaveLength(3);
  expect(materials.map((item: { sequence_number: number }) => item.sequence_number).sort()).toEqual([7, 8, 9]);
  for (const material of materials) {
    expect(material.workflow_status).toBe("IN_PROGRESS"); expect(material.validation_status).toBe("NOT_CHECKED");
    expect(material.publication_status).toBe("NOT_PUBLISHED"); expect(material.is_published).toBe(false); expect(material.folder_path).toBeNull();
    expect((await (await page.request.get(`/api/materials/${material.id}/metadata`)).json()).status).toBe("NOT_SCANNED");
  }
  expect((await (await page.request.get(`/api/brands/${brandId}`)).json()).next_sequence_number).toBe(10);
  await details.getByRole("link", { name: "E2EIMPORT_0008_G03", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Historical stone", exact: true })).toBeVisible();
  expect(failures).toEqual([]);
});
