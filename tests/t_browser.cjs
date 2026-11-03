/* Optional browser smoke check. Requires Playwright and the local seeded app on :8082. */
const assert = require("node:assert/strict");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright");

const base = process.env.GARAGEHUB_TEST_URL || "http://127.0.0.1:8082";
assert.equal(new URL(base).hostname, "127.0.0.1", "Only the isolated local app is allowed");

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await context.route("**/*", (route) => route.request().url().startsWith(base) ? route.continue() : route.abort());
  const page = await context.newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    await page.goto(base + "/?dev=client#/my");
    await page.locator('[data-action="open"]').first().click();
    const seen = [];
    page.on("response", async (r) => {
      if (r.request().method() === "POST" && /\/unlock$/.test(r.url()) && r.status() === 202) seen.push((await r.json()).id);
    });
    await page.getByRole("button", { name: "Открыть бокс", exact: true }).dblclick();
    for (let i = 0; i < 20; i++) {
      if ((await page.locator('[role="status"]').innerText()).includes("подтверждение разблокировки")) break;
      await page.getByRole("button", { name: "Проверить результат", exact: true }).click();
      await page.waitForTimeout(300);
    }
    assert.match(await page.locator('[role="status"]').innerText(), /Тестовый замок.*подтверждение разблокировки/);
    assert.equal(new Set(seen).size, 1, "Repeated click must register one command");
    await page.screenshot({ path: path.join(os.tmpdir(), "garagehub-client-unlock.png"), fullPage: true });
    await page.getByRole("button", { name: "Вернуться к брони" }).click();
    await page.waitForTimeout(3100);
    await page.getByRole("button", { name: "Открыть бокс", exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[role="status"]'));
    assert.equal(new Set(seen).size, 2, "A new explicit action permits reentry");

    const admin = await context.newPage();
    admin.on("pageerror", (e) => errors.push(e.message));
    await admin.goto(base + "/?dev=admin#/admin");
    await admin.getByRole("button", { name: "Замки", exact: true }).click();
    await admin.locator('[data-action="manualUnlock"]').last().click();
    await admin.locator('[data-input="reason"]').fill("Проверка локального стенда оператором");
    await admin.getByRole("button", { name: "Отправить команду", exact: true }).click();
    await admin.getByRole("button", { name: "Проверить результат", exact: true }).click();
    await admin.locator('[data-action="refreshLocks"]').waitFor();
    await admin.locator('.sheet').waitFor({ state: 'detached' });
    assert(await admin.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Mobile layout must not overflow");
    await admin.screenshot({ path: path.join(os.tmpdir(), "garagehub-admin-locks.png"), fullPage: true });
    await admin.getByRole("button", { name: "Объекты", exact: true }).click();
    await admin.locator('[data-action="object"]').first().click();
    await admin.getByRole("button", { name: "Настроить замок", exact: true }).click();
    await admin.getByRole("heading", { name: "Назначить замок", exact: true }).waitFor();
    assert.deepEqual(errors, []);
    console.log("Browser OK: client unlock, duplicate click, reentry, operator unlock, device settings; no JS errors.");
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
