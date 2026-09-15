import { expect, test } from "@playwright/test";
import { processorEmail, retainedPass, signInThroughApi, signInThroughUi, fillPassword, testPassword } from "./auth-helpers";
import { runManifest } from "./run-manifest";

test("administrator creates, provisions, changes and disables an account through the UI", async ({ page, browser }) => {
  await signInThroughApi(page);
  await page.goto("/settings/users");
  await expect(page.getByRole("heading", { name: "Accounts", exact: true })).toBeVisible();
  const name = `Account UI ${retainedPass ? "retained" : "first"}`;
  const email = `account-ui-${retainedPass ? "retained" : "first"}@example.invalid`;
  const form = page.getByRole("form", { name: "Create account", exact: true });
  await form.getByLabel("Display name").fill(name);
  await form.getByLabel("Email", { exact: true }).fill(email);
  await expect(form.getByLabel("Role")).toHaveValue("PROCESSOR");
  await form.getByRole("button", { name: "Create profile" }).click();
  await expect(page.getByText("Profile created. Use Set or reset access to issue a temporary password.")).toBeVisible();
  const row = page.getByRole("form", { name: `Manage ${name}`, exact: true });
  const issueAccess = async (password: "TEMPORARY" | "USER") => {
    await row.getByRole("button", { name: "Set or reset access" }).click();
    await fillPassword(page, "Your current password", testPassword("ADMIN"));
    await fillPassword(page, "Temporary password", testPassword(password));
    await fillPassword(page, "Confirm temporary password", testPassword(password));
    await page.getByRole("button", { name: "Issue temporary access" }).click();
    await expect(page.getByText("Temporary access issued. The user must change their password at next sign-in.")).toBeVisible();
  };
  await issueAccess("TEMPORARY");
  const recipientContext = await browser.newContext();
  try {
    const recipient = await recipientContext.newPage();
    await recipient.goto(runManifest.frontendUrl);
    await signInThroughUi(recipient, email, testPassword("TEMPORARY"));
    await expect(recipient.getByRole("heading", { name: "Change password", exact: true })).toBeVisible();
    await row.getByLabel("Role").selectOption("PRODUCTION_LEAD");
    await row.getByRole("button", { name: "Save role and status" }).click();
    await expect(page.getByText("Account updated. Existing sessions have been revoked where required.")).toBeVisible();
    expect((await recipient.request.get("/api/auth/session")).status()).toBe(401);
    await recipient.reload();
    await signInThroughUi(recipient, email, testPassword("TEMPORARY"));
    await expect(recipient.getByRole("heading", { name: "Change password", exact: true })).toBeVisible();
    await issueAccess("USER");
    expect((await recipient.request.get("/api/auth/session")).status()).toBe(401);
    await row.getByLabel("Active", { exact: true }).uncheck();
    await row.getByRole("button", { name: "Save role and status" }).click();
    await expect(row.getByRole("button", { name: "Set or reset access" })).toBeDisabled();
    await recipient.reload();
    await signInThroughUi(recipient, email, testPassword("USER"));
    await expect(recipient.getByRole("alert")).toContainText("Invalid email or password");
    await expect(recipient.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
    await page.reload();
    await expect(row.getByLabel("Active", { exact: true })).not.toBeChecked();
    await expect(row.getByLabel("Role")).toHaveValue("PRODUCTION_LEAD");
  } finally { await recipientContext.close(); }
});

test("anonymous resource and account-management requests are rejected by the server", async ({ request }) => {
  for (const path of ["/api/companies", "/api/materials", "/api/internal-users"]) {
    expect((await request.get(path)).status()).toBe(401);
  }
  expect((await request.post("/api/internal-users", { data: { display_name: "Forbidden", email: "forbidden@example.invalid", role: "ADMIN" } })).status()).toBe(401);
});

test("forced password change, persisted login, role enforcement and logout use real sessions", async ({ page, playwright }) => {
  await page.goto("/materials");
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await signInThroughUi(page, processorEmail, testPassword(retainedPass ? "USER" : "TEMPORARY"));
  if (!retainedPass) {
    await expect(page.getByRole("heading", { name: "Change password", exact: true })).toBeVisible();
    await expect(page.getByRole("navigation")).toHaveCount(0);
    expect((await page.request.get("/api/materials")).status()).toBe(403);
    await fillPassword(page, "Current password", testPassword("TEMPORARY"));
    await fillPassword(page, "New password", testPassword("USER"));
    await fillPassword(page, "Confirm new password", testPassword("USER"));
    await page.getByRole("button", { name: "Change password", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
    await signInThroughUi(page, processorEmail, testPassword("USER"));
  }
  await expect(page.getByRole("heading", { name: "Materials", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Add material", exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Materials", exact: true })).toBeVisible();
  const auth = await page.request.get("/api/auth/session");
  expect(auth.status()).toBe(200);
  const body = await auth.json();
  expect(body.must_change_password).toBe(false);
  expect(body.user.role).toBe("PROCESSOR");
  expect((await page.request.post("/api/companies", { headers: { Origin: runManifest.frontendUrl, "X-CSRF-Token": body.csrf_token }, data: { name: "Not allowed" } })).status()).toBe(403);
  expect((await page.request.post("/api/companies", { data: { name: "No CSRF" } })).status()).toBe(403);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  const cookies = await page.context().cookies();
  expect(cookies.some((cookie) => cookie.name === "reawote_dev_session" && cookie.httpOnly && cookie.sameSite === "Strict")).toBe(true);
  // Keep a copy only in memory to prove that logout revokes server-side state.
  const stale = await playwright.request.newContext({ baseURL: runManifest.frontendUrl, storageState: await page.context().storageState() });
  try {
    await page.getByLabel("User menu").click();
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
    expect((await stale.get("/api/materials")).status()).toBe(401);
    await page.reload();
    await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  } finally { await stale.dispose(); }
});
