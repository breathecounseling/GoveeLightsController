// Playwright check of More -> Fourth run at phone width (run by tests/test_r10_ui_playwright.py):
// no JS errors, no horizontal scroll at 390 px, 44 px touch targets, the check and add flows.
// usage: NODE_PATH=$(npm root -g) node tests/ui_fourth_run.js http://127.0.0.1:PORT/
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2];
  const out = { errors: [], overflow: [], small: [], steps: [] };
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true });
    page.on('pageerror', e => out.errors.push('pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) out.errors.push('console: ' + m.text()) });
    page.on('dialog', d => d.dismiss());
    await page.addInitScript(() => { try { localStorage.setItem('setupSkip', '1') } catch (e) {} });   // no first-run sheet
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForTimeout(800);
    await page.click('#nav button[data-t="more"]');
    await page.waitForSelector('#frBoxes .frb', { timeout: 8000 });
    out.steps.push('card');
    const width = async (where) => {
      const w = await page.evaluate(() => [document.documentElement.scrollWidth, document.body.scrollWidth, innerWidth]);
      if (w[0] > w[2] || w[1] > w[2]) out.overflow.push(`${where}: ${w.join('/')}`);
    };
    const touch = async (sel, where) => {
      const small = await page.evaluate(s => [...document.querySelectorAll(s)].filter(b => b.offsetParent !== null)
        .map(b => [b.textContent.trim().slice(0, 30), Math.round(b.getBoundingClientRect().height), Math.round(b.getBoundingClientRect().width)])
        .filter(x => x[1] < 44 || x[2] < 44), sel);
      small.forEach(x => out.small.push(`${where}: "${x[0]}" ${x[1]}x${x[2]}`));
    };
    await page.click('#frLadder summary');
    await width('card');
    await touch('#fourthCard button, #fourthCard summary', 'card');
    await page.click('#frBoxes .frb button[data-a="check"]');
    await page.waitForSelector('#frBoxes .frv', { timeout: 15000 });
    out.verdict = await page.textContent('#frBoxes .frv');
    out.steps.push('check');
    await width('after check');
    await page.click('#frBoxes .frb button[data-a="add"]');
    await page.waitForSelector('#sheet.on #frGo');
    out.steps.push('add sheet');
    out.defaultOut = await page.evaluate(() => (document.querySelector('#sheet input[name=frOut]:checked') || {}).value);
    out.lanWarn = await page.evaluate(() => !!document.querySelector('#sheet input[name=frOut][value=lan] ~ span .warn'));
    out.findDisabled = await page.evaluate(() => document.querySelector('#frFind').disabled);
    await page.click('#sheet .chip:has-text("Second roofline")');
    out.join = await page.inputValue('#frJoin');
    out.follow = await page.inputValue('#frFollow');
    out.followH = await page.evaluate(() => Math.round(document.querySelector('#frFollow').getBoundingClientRect().height));
    out.name = await page.inputValue('#frName');
    await page.check('#sheet input[name=frOut][value=cloud]');
    await width('add sheet');
    if (process.argv[4]) { await page.locator('#sheet').screenshot({ path: process.argv[4] }).catch(() => {}) }
    await touch('#sheet .fr button', 'sheet');
    await page.check('#sheet input[name=frOut][value=lan]');
    await page.fill('#frCount', '64');
    await page.click('#frGo');
    await page.waitForSelector('#frBoxes .frb button[data-a="blink"]', { timeout: 8000 });
    out.steps.push('added');
    out.devInfo = await page.textContent('#devInfo');
    await width('after add');
    await page.fill('#frKey', 'abcd1234-ef56-7890-abcd-ef1234567890');
    await page.click('#frKeySave');
    await page.waitForSelector('#frKeyDel', { timeout: 5000 });
    out.cloudText = await page.textContent('#frCloud');
    out.steps.push('key');
    if (process.argv[3]) { await page.locator('#fourthCard').screenshot({ path: process.argv[3] }).catch(() => {}) }
  } catch (e) {
    out.errors.push('flow: ' + e.message.split('\n')[0]);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
