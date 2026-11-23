/* NODE_PATH=/path/to/playwright/node_modules node tests/t_private_link_ui.cjs
   Static fixture only: no database, Telegram or real bookings. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const root = path.resolve(__dirname, '../site');
const booking = {id: 1, status: 'confirmed', total: 500, date: '2026-12-01', start_hour: 10, end_hour: 11,
  is_demo: true, garage: {id: 1, name: 'Тестовый бокс'},
  access_token: '11111111-1111-4111-8111-111111111111', payment: {status: 'paid'}};
const server = http.createServer(async (req, res) => {
  if (req.url === '/') {
    res.setHeader('Content-Type', 'text/html');
    res.end(`<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/css/site.css"><script type="module">import {openBookingDialog} from '/js/booking-details.js';openBookingDialog(${JSON.stringify(booking)});</script>`);
    return;
  }
  try {
    const file = path.resolve(root, '.' + new URL(req.url, 'http://local').pathname);
    if (!file.startsWith(root + path.sep)) throw new Error();
    res.setHeader('Content-Type', ({'.css': 'text/css', '.js': 'text/javascript'})[path.extname(file)] || 'application/octet-stream');
    res.end(await fs.readFile(file));
  } catch { res.statusCode = 404; res.end(); }
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch();
  try {
    for (const [width, reduced] of [[1440, false], [390, false], [320, true]]) {
      const page = await browser.newPage({viewport: {width, height: 844}, reducedMotion: reduced ? 'reduce' : 'no-preference'});
      const errors = []; page.on('pageerror', e => errors.push(e.message));
      await page.goto('http://127.0.0.1:' + server.address().port);
      await page.locator('.booking-dialog[open]').waitFor();
      await page.locator('.booking-dialog').evaluate(el => Promise.all(el.getAnimations().map(a => a.finished)));
      const details = page.locator('.private-booking'), summary = details.locator('summary');
      const initial = await details.evaluate(el => el.offsetHeight);
      await summary.focus(); await page.keyboard.press('Enter');
      if (!reduced) assert(await details.evaluate(el => el.getAnimations().some(a => a.playState === 'running')));
      await page.waitForFunction(() => { const el = document.querySelector('.private-booking'); return el.open && !el.getAnimations().length; });
      assert((await details.evaluate(el => el.offsetHeight)) > initial + 100);
      assert.equal(await details.evaluate(el => el.style.height), '');
      assert.equal(await details.evaluate(el => el.style.overflow), '');
      await summary.click();
      if (!reduced) assert(await details.evaluate(el => el.open && el.getAnimations().some(a => a.playState === 'running')));
      await page.waitForFunction(() => !document.querySelector('.private-booking').open);
      assert.equal(await details.evaluate(el => el.offsetHeight), initial);
      // Reverse a running animation repeatedly; the last intent must win.
      await summary.evaluate(el => { el.click(); el.click(); el.click(); });
      await page.waitForFunction(() => { const el = document.querySelector('.private-booking'); return el.open && !el.getAnimations().length; });
      assert.equal(await summary.getAttribute('aria-expanded'), 'true');
      assert.equal(await details.evaluate(el => el.style.height), '');
      await page.locator('[data-copy-booking]').click();
      await page.waitForFunction(() => document.querySelector('.copy-result').textContent.length > 0);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await summary.focus(); await page.keyboard.press('Space');
      await page.waitForFunction(() => !document.querySelector('.private-booking').open);
      await page.keyboard.press('Escape'); await page.locator('.booking-dialog').waitFor({state: 'detached'});
      assert.deepEqual(errors, []);
      console.log(`PASS ${width}px${reduced ? ' reduced-motion' : ''}: animated expand/collapse, rapid reversal, keyboard, copy, popup close`);
      await page.close();
    }
  } finally { await browser.close(); server.close(); }
})().catch(e => { console.error(e); server.close(); process.exit(1); });
