import { expect, test } from "@playwright/test";

test("OAuth login leaves the Portal as a document navigation", async ({ page }) => {
  let resourceType = "";
  page.on("request", (request) => {
    if (request.url().startsWith("http://127.0.0.1:4321/authorize")) {
      resourceType = request.resourceType();
    }
  });

  await page.goto("/login");
  await page.getByRole("link", { name: "Tiếp tục đăng nhập" }).click();

  await expect(page.getByRole("heading", { name: "Thiết bị của bạn" })).toBeVisible();
  expect(resourceType).toBe("document");
});

test("pairing return path survives the OAuth round trip", async ({ page }) => {
  await page.goto("/pair?request=PAIRCODE1");
  await page.getByRole("link", { name: "Tiếp tục đăng nhập" }).click();

  await expect(page.getByRole("heading", { name: "Xác nhận liên kết" })).toBeVisible();
  await expect(page.getByText("Device A - máy thật", { exact: true })).toBeVisible();
  expect(page.url()).toContain("/pair?request=PAIRCODE1");
});
