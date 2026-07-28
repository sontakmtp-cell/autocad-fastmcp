import { createHash } from "node:crypto";
import { EncryptJWT } from "jose";
import { expect, test, type Browser, type BrowserContext } from "@playwright/test";

const secret = "playwright-session-secret-at-least-32-characters";

async function authenticatedContext(
  browser: Browser,
  owner: "a" | "b" = "a",
  authenticatedAt?: number,
): Promise<BrowserContext> {
  const context = await browser.newContext();
  const expiresAt = Math.floor(Date.now() / 1000) + 3600;
  const ownerKey = `user-${createHash("sha256")
    .update(`http://127.0.0.1:4321/oidc/\0owner-${owner}`)
    .digest("hex")}`;
  const token = await new EncryptJWT({
    subject: `owner-${owner}`,
    displayName: `Owner ${owner.toUpperCase()}`,
    accessToken: `owner-${owner}-token`,
    csrfToken: `csrf-owner-${owner}-token-at-least-thirty-two-characters`,
    expiresAt,
    ...(authenticatedAt === undefined ? {} : { authenticatedAt, ownerKey }),
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

test("missing and stale recent auth cannot approve and preserve the exact return path", async ({ browser }) => {
  for (const authenticatedAt of [undefined, Math.floor(Date.now() / 1000) - 3600]) {
    const context = await authenticatedContext(browser, "a", authenticatedAt);
    const page = await context.newPage();
    await page.goto("/consents/consent-a-stale");
    await expect(page.getByRole("button", { name: /Phê duyệt đúng yêu cầu này/ }))
      .toHaveCount(0);
    const reauth = page.getByRole("link", { name: "Xác thực lại an toàn" });
    await expect(reauth).toHaveAttribute(
      "href",
      /recent=1&returnTo=%2Fconsents%2Fconsent-a-stale/,
    );
    const direct = await context.request.post(
      "/api/bff/consents/consent-a-stale/approve",
      {
        headers: {
          origin: "http://127.0.0.1:3210",
          "content-type": "application/x-www-form-urlencoded",
        },
        form: { csrf: "csrf-owner-a-token-at-least-thirty-two-characters" },
        maxRedirects: 0,
      },
    );
    expect(direct.status()).toBe(303);
    expect(direct.headers().location).toContain(
      "/reauth?returnTo=%2Fconsents%2Fconsent-a-stale",
    );
    await context.close();
  }
});

test("valid recent auth approves exact Gateway records and ignores browser override fields", async ({ browser }) => {
  const context = await authenticatedContext(
    browser,
    "a",
    Math.floor(Date.now() / 1000),
  );
  const page = await context.newPage();
  await page.goto("/consents/consent-a-0001");
  await expect(page.getByText("Tạo hai đối tượng theo preview đã khóa · 2")).toBeVisible();
  await page.locator("form[action$='/approve']").evaluate((form) => {
    for (const [name, value] of [
      ["owner", "owner-b"],
      ["risk", "low"],
      ["assurance", "none"],
      ["effect_summary", "model says safe"],
    ]) {
      const input = document.createElement("input");
      input.name = name;
      input.value = value;
      form.appendChild(input);
    }
  });
  await page.getByRole("button", { name: "Phê duyệt đúng yêu cầu này" }).click();
  await expect(page).toHaveURL(/\/intents\/intent-a-0001\?result=approved$/);
  await expect(page.getByText("Quyết định phê duyệt đã được Gateway ghi nhận."))
    .toBeVisible();
  expect(await page.content()).not.toContain("model says safe");

  await page.goto("/consents/consent-a-deny");
  await page.getByRole("button", { name: "Từ chối yêu cầu này" }).click();
  await expect(page).toHaveURL(/\/intents\/intent-a-deny\?result=denied$/);
  await expect(page.getByText("Quyết định từ chối đã được Gateway ghi nhận."))
    .toBeVisible();
  await context.close();
});

test("origin, CSRF, cross-owner URL, expiry and replay fail closed", async ({ browser }) => {
  const fresh = Math.floor(Date.now() / 1000);
  const context = await authenticatedContext(browser, "a", fresh);
  const csrf = "csrf-owner-a-token-at-least-thirty-two-characters";
  const crossOrigin = await context.request.post(
    "/api/bff/consents/consent-a-stale/approve",
    {
      headers: {
        origin: "http://evil.test",
        "content-type": "application/x-www-form-urlencoded",
      },
      form: { csrf },
      maxRedirects: 0,
    },
  );
  expect(crossOrigin.status()).toBe(403);
  const badCsrf = await context.request.post(
    "/api/bff/consents/consent-a-stale/approve",
    {
      headers: {
        origin: "http://127.0.0.1:3210",
        "content-type": "application/x-www-form-urlencoded",
      },
      form: { csrf: "wrong" },
      maxRedirects: 0,
    },
  );
  expect(badCsrf.status()).toBe(403);

  const page = await context.newPage();
  for (const [consentId, expectedError] of [
    ["consent-a-expired", "expired"],
    ["consent-a-replayed", "conflict"],
    ["consent-a-version", "conflict"],
  ]) {
    const response = await context.request.post(
      `/api/bff/consents/${consentId}/approve`,
      {
        headers: {
          origin: "http://127.0.0.1:3210",
          "content-type": "application/x-www-form-urlencoded",
        },
        form: { csrf },
        maxRedirects: 0,
      },
    );
    expect(response.status()).toBe(303);
    expect(response.headers().location).toContain(`error=${expectedError}`);
  }
  await page.goto("/consents/consent-a-expired");
  await expect(page.getByRole("button", { name: /Phê duyệt/ })).toHaveCount(0);
  await page.goto("/consents/consent-a-replayed");
  await expect(page.getByRole("button", { name: /Phê duyệt/ })).toHaveCount(0);
  await context.close();

  const ownerB = await authenticatedContext(browser, "b", fresh);
  const ownerBPage = await ownerB.newPage();
  await ownerBPage.goto("/intents/intent-a-stale");
  await expect(ownerBPage.getByText(/This page could not be found|không tìm thấy/i))
    .toBeVisible();
  expect(await ownerBPage.content()).not.toContain("drawing33-document");
  await ownerB.close();
});

test("old receipt has no rollback action and unknown outcome has no retry-write", async ({ browser }) => {
  const context = await authenticatedContext(
    browser,
    "a",
    Math.floor(Date.now() / 1000),
  );
  const page = await context.newPage();
  await page.goto("/receipts/receipt-a-0001");
  await expect(page.getByText("Rollback unavailable: no Phase-7 checkpoint")).toBeVisible();
  await expect(page.getByRole("button", { name: /rollback/i })).toHaveCount(0);

  await page.goto("/jobs/job-unknown-a-0001");
  await expect(page.getByText("Không có nút chạy lại write. Hãy làm mới evidence hoặc dùng support ID từ Desktop Agent."))
    .toBeVisible();
  await expect(page.getByRole("button", { name: /retry|chạy lại|commit/i })).toHaveCount(0);
  await context.close();
});
