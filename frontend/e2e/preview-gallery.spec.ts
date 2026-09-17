import { expect, test, type Locator } from "@playwright/test";
import { createHash } from "node:crypto";
import { signInThroughApi } from "./auth-helpers";
import { runManifest } from "./run-manifest";

async function decoded(image: Locator) {
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((element) => {
    const image = element as HTMLImageElement;
    return image.complete && image.naturalWidth === 768 && image.naturalHeight === 432;
  })).toBe(true);
}

test("real preview gallery and comparison survive a retained-data restart", async ({ page }) => {
  await signInThroughApi(page);
  const failures: string[] = [], reads: Promise<void>[] = [];
  function previewLabel(value: string) {
    const url = new URL(value);
    return url.pathname + ":" + (["front.png", "side.png"].includes(url.searchParams.get("name") ?? "") ? url.searchParams.get("name") : "other");
  }
  page.on("pageerror", () => failures.push("browser error"));
  page.on("console", (message) => { if (["error", "warning"].includes(message.type())) failures.push("console diagnostic"); });
  page.on("requestfailed", (request) => failures.push(`request failed: ${previewLabel(request.url())}: ${request.resourceType()}: ${request.failure()?.errorText ?? "unknown"}`));
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (response.status() >= 400) failures.push(`HTTP ${response.status()}: ${path}`);
    if (!path.startsWith("/api/") || path.startsWith("/api/auth/")) return;
    reads.push((async () => {
      const body = await response.body();
      if (path.endsWith("/preview")) {
        expect(response.headers()["content-type"]).toBe("image/jpeg");
        expect(response.headers()["cache-control"]).toBe("no-store");
        expect(body.length).toBeLessThanOrEqual(2 * 1024 ** 2);
        expect(body.subarray(0, 3).equals(Buffer.from([255, 216, 255]))).toBe(true);
        expect(body.subarray(-2).equals(Buffer.from([255, 217]))).toBe(true);
        expect(createHash("sha256").update(body).digest("hex")).toBe(response.headers()["x-preview-sha256"]);
      }
      const text = body.toString("utf8");
      expect(/raw_content|source_content|PRIVATE_SYNTHETIC|\/e2e-materials|[A-Za-z]:\\/i.test(text)).toBe(false);
      expect(text.includes(runManifest.materialsRoot)).toBe(false);
    })().catch(() => { failures.push(`response contract failed: ${path}`); }));
  });
  // Reconcile only these two owned fixtures so this test also works independently
  // of the Done scenario's execution order. Never relink an existing folder.
  const session = await (await page.request.get("/api/auth/session")).json();
  for (const fixture of [runManifest.state.valid, runManifest.state.approval]) {
    const current = await (await page.request.get(`/api/materials/${fixture.id}`)).json();
    if (!current.folder_path) {
      const linked = await page.request.post(`/api/materials/${fixture.id}/folder-link`, {
        headers: { Origin: runManifest.frontendUrl, "X-CSRF-Token": session.csrf_token }, data: { folder_path: fixture.relativePath },
      });
      expect(linked.status()).toBe(200);
    } else expect(current.folder_path).toBe(fixture.relativePath);
  }
  await page.goto(`/materials/${runManifest.state.valid.id}`);
  // Finish all independent detail reads before navigating away.
  for (const name of ["Reload identity status", "Reload content approval", "Reload technical review", "Reload source review"])
    await expect(page.getByRole("button", { name, exact: true })).toBeVisible();
  await expect(page.getByRole("article", { name: "Publication content", exact: true }).getByText(/^Revision \d+ ·/)).toBeVisible();
  const gallery = page.getByRole("article", { name: "Preview gallery", exact: true });
  await gallery.getByRole("button", { name: "Open preview gallery" }).click();
  await decoded(gallery.getByRole("img", { name: "Preview: front.png" }));
  await gallery.getByLabel("Preview image").selectOption("side.png");
  await decoded(gallery.getByRole("img", { name: "Preview: side.png" }));
  await page.getByRole("link", { name: "Compare", exact: true }).click();
  await page.getByRole("combobox", { name: "Left material", exact: true }).selectOption(runManifest.state.valid.id);
  await page.getByRole("combobox", { name: "Right material", exact: true }).selectOption(runManifest.state.approval.id);
  const left = page.getByRole("region", { name: "Left material", exact: true }), right = page.getByRole("region", { name: "Right material", exact: true });
  await decoded(left.getByRole("img")); await decoded(right.getByRole("img"));
  await right.getByLabel("Preview image").selectOption("side.png"); await decoded(right.getByRole("img", { name: "Preview: side.png" }));
  expect(await left.getByRole("img").getAttribute("src")).toMatch(/^blob:/);
  await page.locator(".material-comparison").screenshot({ path: test.info().outputPath("preview-comparison.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await decoded(left.getByRole("img")); await decoded(right.getByRole("img"));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.locator(".material-comparison").screenshot({ path: test.info().outputPath("preview-comparison-mobile.png") });
  await Promise.all(reads);
  expect(failures).toEqual([]);
  expect(await page.locator("body").innerText()).not.toMatch(/raw_content|source_content|\/e2e-materials|[A-Za-z]:\\/i);
});
