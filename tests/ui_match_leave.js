// Playwright check (run by tests/test_r13_fixes.py): tap a colour in Match the runs and leave
// More before the start reply comes back (slow while the lights switch on). The test colour
// must not keep running on the house. usage: NODE_PATH=$(npm root -g) node tests/ui_match_leave.js URL
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2];
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true });
    page.on('pageerror', e => out.errors.push('pageerror: ' + e.message));
    await page.addInitScript(() => { try { localStorage.setItem('setupSkip', '1') } catch (e) {} });
    await page.route('**/api/match', async route => {       // the start reply is slow to come back
      const body = route.request().postData() || '';
      const resp = await route.fetch();
      if (/"start"/.test(body)) await new Promise(r => setTimeout(r, 1500));
      await route.fulfill({ response: resp });
    });
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForTimeout(800);
    await page.click('#nav button[data-t="more"]');
    await page.waitForSelector('#mtRuns .mtr', { timeout: 8000 });
    const api = async p => page.evaluate(async p => (await fetch('/api/' + p)).json(), p);
    await page.click('#mtSw button[data-k="red"]');
    await page.waitForTimeout(200);
    out.during = (await api('status')).match;               // the server has started it
    await page.click('#nav button[data-t="play"]');          // left More before the reply
    await page.waitForTimeout(3000);
    out.after = (await api('status')).match;
  } catch (e) {
    out.errors.push('flow: ' + e.message);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
