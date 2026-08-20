import { expect, test } from "@playwright/test";

async function register(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "还没有账户？创建账户" }).click();
  await page.getByLabel("显示名称").fill("E2E 用户");
  await page.getByLabel("邮箱").fill(`e2e-${Date.now()}-${Math.random().toString(16).slice(2)}@example.test`);
  await page.getByLabel("密码").fill("correct-horse-battery-staple");
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page.getByRole("button", { name: /退出/ })).toBeVisible();
}

async function confirmHealthyAdultBoundary(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: /编辑档案/ }).click();
  await page.locator(".drawer").getByText("已确认无已知过敏原", { exact: true }).click();
  await expect(page.locator(".drawer").getByRole("radio", { name: "已确认无已知过敏原" })).toBeChecked();
  await page.getByRole("button", { name: /完成|关闭|保存/ }).last().click();
  await expect(page.getByRole("button", { name: "生成今日计划" })).toBeEnabled();
  const history = page.getByRole("checkbox", { name: /使用最近的已采用计划/ });
  if (await history.isChecked() && await history.isEnabled()) await history.uncheck();
}

test("planning, history, assistant and safety navigation remain reachable", async ({ page }) => {
  await register(page);
  await expect(page.getByText("仅适用于健康成年人").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: /把约束/ })).toBeVisible();
  await page.getByRole("button", { name: /历史计划/ }).click();
  await expect(page.getByRole("heading", { name: "历史计划" })).toBeVisible();
  await page.getByRole("button", { name: /对话助手/ }).click();
  await expect(page.getByRole("heading", { name: /围绕计划/ })).toBeVisible();
});

test("planning reaches a completed durable run through SSE and can be adopted", async ({ page }) => {
  await register(page);
  await confirmHealthyAdultBoundary(page);
  await page.getByRole("button", { name: "生成今日计划" }).click();
  await expect(page.getByText("计划已完成")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: /采用此计划/ }).click();
  await expect(page.getByText(/已加入历史计划|已采用/).first()).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: /历史计划/ }).click();
  await page.getByRole("button", { name: /查看详情与反馈/ }).click();
  await page.getByRole("button", { name: "删除" }).click();
  await expect(page.getByRole("alertdialog", { name: "删除这条历史计划？" })).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();
  await expect(page.getByRole("alertdialog", { name: "删除这条历史计划？" })).not.toBeVisible();
});

test("an infeasible run pauses, accepts a versioned decision and resumes", async ({ page }) => {
  test.setTimeout(90_000);
  await register(page);
  await confirmHealthyAdultBoundary(page);
  await page.getByRole("textbox", { name: "自然语言需求" }).fill("时间 1 分钟，蛋白质 90g，1500-1700 kcal");
  await page.locator(".constraint-grid label").filter({ hasText: "时间" }).getByRole("textbox").fill("1");
  await page.getByRole("button", { name: "生成今日计划" }).click();
  await expect(page.getByText("需要确认调整")).toBeVisible({ timeout: 45_000 });
  await page.locator(".negotiation").getByRole("radio").first().check();
  await page.locator(".negotiation").getByRole("button", { name: "采用调整并继续" }).click();
  await expect(page.getByText("计划已完成")).toBeVisible({ timeout: 30_000 });
});
