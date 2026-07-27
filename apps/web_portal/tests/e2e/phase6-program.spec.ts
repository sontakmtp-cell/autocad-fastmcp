import { createHash } from "node:crypto";
import { EncryptJWT } from "jose";
import { expect, test, type Browser, type BrowserContext } from "@playwright/test";

const secret = "playwright-session-secret-at-least-32-characters";

async function authenticatedContext(browser: Browser, owner = "a"): Promise<BrowserContext> {
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

test("renders program, exact binding, preview, receipt and validation summaries", async ({ browser }) => {
  const context = await authenticatedContext(browser);
  const page = await context.newPage();

  await page.goto("/programs/program-a-0001/revisions/1");
  await expect(page.getByRole("heading", { name: "CAD Program program-a-0001" })).toBeVisible();
  await expect(page.getByText("managed_dotnet_r25 · primary")).toBeVisible();
  await expect(page.getByText("Gateway xác nhận pilot write đang được hiển thị")).toBeVisible();

  await page.goto("/previews/preview-a-0001");
  await expect(page.getByText(
    "DWG chưa thay đổi. Preview thành công không có nghĩa là đã được phê duyệt.",
  )).toBeVisible();
  await expect(page.getByText("transaction aborted")).toBeVisible();
  await expect(page.getByRole("button", { name: /phê duyệt|approve|xác nhận commit/i }))
    .toHaveCount(0);

  await page.goto("/receipts/receipt-a-0001");
  await expect(page.getByRole("heading", { name: "Receipt receipt-a-0001" })).toBeVisible();
  await expect(page.getByText("revision-after-002")).toBeVisible();

  await page.goto("/validations/validation-a-0001");
  await expect(page.getByText("Kết quả:")).toBeVisible();
  await expect(page.getByText("entity count match")).toBeVisible();
  await context.close();
});

test("shows invalidation and outcome unknown without a blind retry action", async ({ browser }) => {
  const context = await authenticatedContext(browser);
  const page = await context.newPage();

  await page.goto("/previews/preview-a-stale");
  await expect(page.getByText("Môi trường thực thi đã thay đổi. Hãy tạo preview mới."))
    .toBeVisible();

  await page.goto("/jobs/job-unknown-a-0001");
  await expect(page.getByText(/Hệ thống sẽ không tự chạy lại/)).toBeVisible();
  await expect(page.getByText(/Không có nút chạy lại write/)).toBeVisible();
  await expect(page.getByRole("button", { name: /retry|chạy lại|commit/i })).toHaveCount(0);
  await context.close();
});

test("cross-owner direct resource guesses are not found", async ({ browser }) => {
  const context = await authenticatedContext(browser, "b");
  const page = await context.newPage();
  await page.goto("/previews/preview-a-0001");
  await expect(page.getByText(/This page could not be found|không tìm thấy/i)).toBeVisible();
  expect(await page.content()).not.toContain("drawing33-document");
  await context.close();
});
