/* NODE_PATH=/path/to/playwright/node_modules node tests/t_payment_restore_ui.cjs
   BASE_URL may point to the deployed site; all API calls remain intercepted fixtures. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises'), http = require('node:http'), path = require('node:path');
const root = path.resolve(__dirname, '../site');
const token = '11111111-1111-4111-8111-111111111111';
const access = '22222222-2222-4222-8222-222222222222';
const storageKey = 'gh_demo_payment_1';
const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
const garage = {id: 1, name: 'Тест восстановления', district: 'Москва', photos: [], price: 500,
  type: 'garage', description: '', open_hour: 9, close_hour: 22, demo_payment: true};
const server = http.createServer(async (req, res) => {
  try {
    const file = path.resolve(root, '.' + new URL(req.url, 'http://local').pathname);
    assert(file.startsWith(root + path.sep));
    res.setHeader('Content-Type', ({'.js':'text/javascript', '.css':'text/css', '.html':'text/html', '.svg':'image/svg+xml'})[path.extname(file)] || 'application/octet-stream');
    res.end(await fs.readFile(file));
  } catch { res.statusCode = 404; res.end(); }
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch();
  const base = process.env.BASE_URL || 'http://127.0.0.1:' + server.address().port;
  try {
    for (const width of [1440, 390]) {
      for (const scenario of ['confirmed', 'past', 'cancelled', 'expired', 'pending', 'network-error', 'new']) {
        let booking = {id: 1, status: 'confirmed', is_past: scenario === 'past', is_demo: true, can_cancel: scenario !== 'past',
          date: tomorrow, start_hour: 10, end_hour: 11, total: 500, access_token: access, garage,
          support: {message: 'Поддержка скоро появится'},
          payment: {mode: 'demo', token, status: 'paid', expires_at: new Date(Date.now() + 600000).toISOString()}};
        if (['pending', 'new'].includes(scenario)) booking = {...booking, status: 'pending', payment: {...booking.payment, status: 'awaiting'}};
        if (['cancelled', 'expired'].includes(scenario)) booking = {...booking, status: 'cancelled', can_cancel: false,
          payment: {...booking.payment, status: scenario}};
        if (scenario === 'past') booking.date = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
        let creates = 0, payments = 0;
        const p = await browser.newPage({viewport: {width, height: 900}});
        const errors = []; p.on('pageerror', e => errors.push(e.message));
        await p.addInitScript(({key, token, seed}) => {
          if (seed && !sessionStorage.getItem('qa-payment-seeded')) {
            sessionStorage.setItem(key, token);
            sessionStorage.setItem('qa-payment-seeded', '1');
          }
        }, {key: storageKey, token, seed: scenario !== 'new'});
        await p.route('**/api/**', async route => {
          const req = route.request(), url = new URL(req.url());
          let json;
          if (url.pathname === '/api/garages/1') json = garage;
          else if (url.pathname === '/api/garages/1/availability') json = {open_hour: 9, close_hour: 22,
            days: [{date: tomorrow, free: 13, blocked: []}]};
          else if (url.pathname === '/api/public/demo-payment') {
            assert.equal(req.headers().authorization, 'Demo ' + token);
            if (scenario === 'network-error') return route.fulfill({status: 503, json: {error: {message: 'Test failure'}}});
            if (req.method() === 'POST') {
              assert.equal(req.postDataJSON().action, 'pay'); payments++;
              booking = {...booking, status: 'confirmed', payment: {...booking.payment, status: 'paid'}};
            }
            json = booking;
          } else if (url.pathname === '/api/public/bookings') { creates++; json = booking; }
          else if (url.pathname === '/api/public/my-bookings') json = {bookings: [booking]};
          else throw Error('Unexpected API request: ' + url.pathname);
          return route.fulfill({json});
        });
        await p.goto(base + '/garage.html?id=1');
        await p.locator('#booking').waitFor();
        if (scenario === 'confirmed') {
          await p.locator('#booking [data-open-booking]').waitFor();
          assert.equal(await p.locator('dialog[open]').count(), 0);
          await p.reload(); await p.locator('#booking [data-open-booking]').waitFor();
          assert.equal(await p.locator('dialog[open]').count(), 0);
          await p.locator('#booking [data-open-booking]').click();
          await p.locator('.booking-dialog[open]').waitFor();
        } else if (['past', 'cancelled', 'expired'].includes(scenario)) {
          await p.waitForFunction(key => sessionStorage.getItem(key) === null, storageKey);
          assert.equal(await p.locator('dialog[open]').count(), 0);
          assert(await p.locator('#booking [name=phone]').isVisible());
          assert.equal(await p.locator('#booking [data-open-booking]').count(), 0);
          assert.equal(await p.evaluate(() => JSON.parse(localStorage.getItem('gh_website_bookings_v1'))[0].token), access);
          await p.reload(); await p.locator('#booking [name=phone]').waitFor();
          assert.equal(await p.locator('dialog[open]').count(), 0);
          await p.goto(base + '/bookings.html'); await p.locator('.my-booking-row').waitFor();
          assert.equal(await p.locator('dialog[open]').count(), 0);
          await p.locator('.my-booking-row').click(); await p.locator('.booking-dialog[open]').waitFor();
        } else if (scenario === 'pending') {
          await p.locator('[data-resume-payment]').waitFor();
          assert.equal(await p.locator('dialog[open]').count(), 0);
          await p.reload(); await p.locator('[data-resume-payment]').waitFor();
          assert.equal(await p.locator('dialog[open]').count(), 0);
          await p.locator('[data-resume-payment]').click(); await p.locator('.payment-dialog[open]').waitFor();
          await p.locator('[data-pay]').click(); await p.locator('.booking-dialog[open]').waitFor();
          assert.equal(payments, 1);
          await p.reload(); await p.locator('#booking [data-open-booking]').waitFor();
          assert.equal(await p.locator('dialog[open]').count(), 0);
        } else if (scenario === 'new') {
          assert.equal(await p.locator('dialog[open]').count(), 0);
          await p.fill('[name=name]', 'Тест формы'); await p.fill('[name=phone]', '9991234567');
          await p.check('[name=consent]'); await p.locator('#booking button[type=submit]').click();
          await p.locator('.payment-dialog[open]').waitFor(); await p.locator('[data-pay]').click();
          await p.locator('.booking-dialog[open]').waitFor(); assert.equal(creates, 1); assert.equal(payments, 1);
        } else {
          await p.waitForLoadState('networkidle');
          assert.equal(await p.locator('dialog[open]').count(), 0);
          assert(await p.locator('#booking [name=phone]').isVisible());
          assert.equal(await p.evaluate(key => sessionStorage.getItem(key), storageKey), token);
        }
        if (scenario !== 'new') assert.equal(creates, 0, 'Loading a page must not create a booking');
        assert(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        assert.deepEqual(errors, []);
        console.log(`PASS ${width}px ${scenario}: quiet page load, explicit actions, preserved booking access`);
        await p.close();
      }
    }
  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error); server.close(); process.exit(1); });
