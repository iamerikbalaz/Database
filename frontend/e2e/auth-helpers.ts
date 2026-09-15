import type { Page } from "@playwright/test";
import { runManifest } from "./run-manifest";

export function testPassword(name: "ADMIN" | "TEMPORARY" | "USER"): string {
  const value = process.env[`E2E_${name}_PASSWORD`];
  if (!value) throw new Error("Runner did not supply ephemeral authentication credentials.");
  return value;
}
export const retainedPass = process.env.E2E_RETAINED_PASS === "1";
export const adminEmail = "e2e.operator@example.invalid";
export const processorEmail = "e2e.processor@example.invalid";

export async function signInThroughApi(page: Page) {
  let response;
  try {
    response = await page.request.post("/api/auth/login", {
      headers: { Origin: runManifest.frontendUrl },
      data: { email: adminEmail, password: testPassword("ADMIN") },
    });
  } catch { throw new Error("E2E authentication request failed."); }
  if (response.status() !== 200) throw new Error(`E2E authentication returned ${response.status()}.`);
}

export async function fillPassword(page: Page, label: string, value: string) {
  // Do not propagate Playwright call logs containing a fill argument on failure.
  try { await page.getByLabel(label, { exact: true }).fill(value); }
  catch { throw new Error(`Could not enter the ${label.toLowerCase()} field.`); }
}

export async function signInThroughUi(page: Page, email: string, password: string) {
  await page.getByLabel("Email", { exact: true }).fill(email);
  await fillPassword(page, "Password", password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}
