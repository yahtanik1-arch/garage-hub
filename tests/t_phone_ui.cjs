/* NODE_PATH=/path/to/playwright/node_modules node tests/t_phone_ui.cjs
   Only local static files and fake API responses; never submits a real booking. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const root = path.resolve(__dirname, '../site');
const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
const garage = {id: 1, name: 'ТЕСТ формы', district: 'Москва', photos: [], price: 500,
  type: 'garage', description: '', open_hour: 9, close_hour: 22};
let requests = [];
const server = http.createServer(async (req, res) => {
  const json = data => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); };
  if (req.url === '/api/garages/1') return json(garage);
  if (req.url === '/api/garages/1/availability') return json({open_hour: 9, close_hour: 22,
    days: [{date: tomorrow, free: 13, blocked: []}]});
  if (req.url === '/api/public/bookings' && req.method === 'POST') {
    let body = ''; for await (const chunk of req) body += chunk;
    requests.push(JSON.parse(body)); res.statusCode = 201;
    return json({id: 1, date: tomorrow, start_hour: 10, end_hour: 11, total: 500, status: 'pending', garage, support: {message: 'Поддержка скоро появится'}});
  }
  try {
    const filename = path.resolve(root, '.' + new URL(req.url, 'http://local').pathname);
    if (!filename.startsWith(root + path.sep)) throw new Error();
    const data = await fs.readFile(filename);
    const mime = {'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml'};
    res.setHeader('Content-Type', mime[path.extname(filename)] || 'application/octet-stream'); res.end(data);
  } catch { res.statusCode = 404; res.end(); }
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true});
  try {
    for (const width of [1440, 390]) {
      requests = [];
      const page = await browser.newPage({viewport: {width, height: 900}, isMobile: width === 390, hasTouch: width === 390});
      const errors = []; page.on('pageerror', e => errors.push(e.message));
      await page.goto(`http://127.0.0.1:${server.address().port}/garage.html?id=1`);
      const phone = page.locator('[name=phone]'), consent = page.locator('[name=consent]');
      await phone.fill('99912'); await consent.scrollIntoViewIfNeeded();
      // A single click/tap both blurs the incomplete phone and checks consent.
      await (width === 390 ? consent.tap() : consent.click());
      assert(await consent.isChecked(), `${width}px: first consent click was lost`);
      assert(await page.locator('#phone-error').isVisible());
      await phone.fill('9991234567'); await phone.pressSequentially('12345');
      assert.equal(await phone.inputValue(), '999-123-45-67');
      assert.equal(await phone.evaluate(el => el.checkValidity()), true);
      assert.equal(await page.locator('#phone-error').isVisible(), false);
      // Mobile keyboard fallback when beforeinput cannot cancel the insertion.
      await phone.evaluate(el => { el.value += '8'; el.dispatchEvent(new InputEvent('input', {bubbles: true, data: '8', inputType: 'insertText'})); });
      assert.equal(await phone.inputValue(), '999-123-45-67');
      assert.equal(await phone.evaluate(el => el.checkValidity()), true);
      await page.fill('[name=name]', 'Тест формы');
      await page.selectOption('[name=start]', '10'); await page.selectOption('[name=end]', '11');
      // Also verify the label checks/unchecks immediately after leaving a valid phone.
      await phone.focus(); await consent.click(); assert.equal(await consent.isChecked(), false);
      await phone.focus(); await consent.click(); assert(await consent.isChecked());
      await page.locator('#booking button[type=submit]').click();
      await page.locator('.booking-dialog[open]').waitFor();
      assert.equal(await page.locator('#booking .booking-details').count(), 0);
      assert.equal(requests.length, 1); assert.equal(requests[0].phone, '+79991234567');
      assert.deepEqual(errors, []);
      console.log(`PASS ${width}px: first consent click/tap, phone blur, excess digits, mobile input, submission`);
      await page.close();
    }
  } finally { await browser.close(); server.close(); }
})().catch(e => { console.error(e); server.close(); process.exit(1); });
