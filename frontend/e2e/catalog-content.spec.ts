import { expect, test } from "@playwright/test";
import { runManifest } from "./run-manifest";
import { retainedPass, signInThroughApi } from "./auth-helpers";

test("catalog values, material drafts and revision history persist through restart", async ({ page }) => {
  await signInThroughApi(page);
  const fixture = runManifest.state.content; const path = `/api/materials/${fixture.id}`;
  if (!retainedPass) {
    await page.goto("/catalog");
    await page.getByLabel("Catalog value", { exact: true }).fill("E2E Natural Stone");
    await page.getByRole("button", { name: "Create catalog value" }).click();
    await expect(page.getByRole("article", { name: "Online categories" }).getByText("E2E Natural Stone", { exact: true })).toBeVisible();
    await page.getByRole("combobox", { name: "Value type", exact: true }).selectOption("collections");
    await page.getByRole("combobox", { name: "Collection brand", exact: true }).selectOption(runManifest.state.brandId);
    await page.getByLabel("Catalog value", { exact: true }).fill("E2E Architectural Series");
    await page.getByRole("button", { name: "Create catalog value" }).click();
    await expect(page.getByRole("article", { name: "Brand collections" }).getByText("E2E Architectural Series", { exact: true })).toBeVisible();
    await page.goto(`/materials/${fixture.id}`);
    let panel = page.getByRole("article", { name: "Publication content", exact: true });
    await panel.getByRole("textbox", { name: "Description", exact: true }).fill("Synthetic material for catalog verification.");
    await panel.getByRole("spinbutton", { name: "Credits", exact: true }).fill("8");
    await panel.getByRole("textbox", { name: "Tags, one per line", exact: true }).fill("matte\nMatte\nstone");
    await panel.getByRole("checkbox", { name: "E2E Natural Stone", exact: true }).check();
    await panel.getByRole("checkbox", { name: "E2E Architectural Series", exact: true }).check();
    await panel.getByLabel("Reason for content change").fill("Classify synthetic E2E material");
    await panel.getByRole("button", { name: "Save publication draft" }).click();
    await expect(panel.getByText(/Revision 1 · Manual draft/)).toBeVisible();
    panel = page.getByRole("article", { name: "Publication content", exact: true });
    await panel.getByRole("spinbutton", { name: "Credits", exact: true }).fill("12");
    await panel.getByLabel("Reason for content change").fill("Adjust synthetic credits");
    await panel.getByRole("button", { name: "Save publication draft" }).click();
    await expect(panel.getByText(/Revision 2 · Manual draft/)).toBeVisible();
    await page.goto("/catalog");
    for (const action of ["Deactivate", "Reactivate"]) {
      await page.getByRole("button", { name: `${action} E2E Natural Stone`, exact: true }).click();
      await page.getByLabel("Reason for catalog change").fill(`${action} synthetic category`);
      await page.getByRole("button", { name: "Confirm availability change", exact: true }).click();
      await expect(page.getByRole("button", { name: `${action === "Deactivate" ? "Reactivate" : "Deactivate"} E2E Natural Stone`, exact: true })).toBeVisible();
    }
  }
  await page.goto(`/materials/${fixture.id}`);
  const panel = page.getByRole("article", { name: "Publication content", exact: true });
  await expect(panel.getByText(/Revision 2 · Manual draft/)).toBeVisible();
  await expect(panel.getByRole("textbox", { name: "Description", exact: true })).toHaveValue("Synthetic material for catalog verification.");
  await expect(panel.getByRole("spinbutton", { name: "Credits", exact: true })).toHaveValue("12");
  await expect(panel.getByRole("textbox", { name: "Tags, one per line", exact: true })).toHaveValue("matte\nstone");
  await expect(panel.getByRole("checkbox", { name: "E2E Natural Stone", exact: true })).toBeChecked();
  await expect(panel.getByRole("checkbox", { name: "E2E Architectural Series", exact: true })).toBeChecked();
  await panel.getByText("Content history", { exact: true }).click();
  await panel.getByRole("button", { name: "Load content history" }).click();
  await expect(panel.getByText("Revision 1", { exact: true })).toBeVisible();
  await expect(panel.getByText("Revision 2", { exact: true })).toBeVisible();
  const current = await (await page.request.get(path + "/content")).json();
  expect(current.revision).toBe(2); expect(current.collections[0].brand_id).toBe(runManifest.state.brandId);
  expect(current.categories[0].version).toBe(3); expect(current.categories[0].is_active).toBe(true);
  const history = await (await page.request.get(path + "/content-history")).json();
  expect(history).toHaveLength(2); expect(history.map((item: { snapshot: { credits: number } }) => item.snapshot.credits)).toEqual([12, 8]);
  expect((await (await page.request.get(path + "/review")).json()).generation).toBe(4);
  const audit = await (await page.request.get("/api/catalog-audit")).json();
  expect(audit).toHaveLength(4);
});
