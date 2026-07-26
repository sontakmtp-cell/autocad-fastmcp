import { createHash } from "node:crypto";
import { EncryptJWT } from "jose";
import { expect, test, type Browser, type BrowserContext } from "@playwright/test";

const secret = "playwright-session-secret-at-least-32-characters";

async function authenticatedContext(
  browser: Browser,
  owner: "a" | "b",
): Promise<BrowserContext> {
  const context = await browser.newContext();
  const expiresAt = Math.floor(Date.now() / 1000) + 3600;
  const token = await new EncryptJWT({
    subject: `owner-${owner}`,
    displayName: `Owner ${owner.toUpperCase()}`,
    accessToken: `owner-${owner}-token`,
    csrfToken: `csrf-owner-${owner}-token-at-least-thirty-two-characters`,
    expiresAt,
  })
    .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
    .setIssuedAt()
    .setExpirationTime(expiresAt)
    .encrypt(createHash("sha256").update(secret, "utf8").digest());

  await context.addCookies([{
    name: "autocad_portal",
    value: token,
    url: "http://127.0.0.1:3210",
    httpOnly: true,
    sameSite: "Lax",
  }]);
  return context;
}

test("two browser owners cannot list or guess each other's device URL", async ({ browser }) => {
  const ownerA = await authenticatedContext(browser, "a");
  const ownerB = await authenticatedContext(browser, "b");
  const pageA = await ownerA.newPage();
  const pageB = await ownerB.newPage();

  await Promise.all([pageA.goto("/devices"), pageB.goto("/devices")]);

  await expect(pageA.getByText("Máy của Owner A")).toBeVisible();
  await expect(pageA.getByText("Máy bí mật của Owner B")).toHaveCount(0);
  await expect(pageB.getByText("Máy bí mật của Owner B")).toBeVisible();
  await expect(pageB.getByText("Máy của Owner A")).toHaveCount(0);

  await pageA.goto("/devices/device-b-0001");
  await expect(pageA.getByRole("heading", { name: "Không tìm thấy thiết bị" })).toBeVisible();
  await expect(pageA.getByText("Máy bí mật của Owner B")).toHaveCount(0);
  expect(await pageA.content()).not.toContain("owner-a-token");

  await ownerA.close();
  await ownerB.close();
});
