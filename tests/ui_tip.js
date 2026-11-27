// Playwright check of More -> Fourth run -> Add it to the show -> "Top of the Peak" on an
// iPhone-sized screen (run by tests/test_r13_tip_ui.py against fake boxes from
// tools/fake_govee.py): no JS errors, no horizontal scroll, 44 px touch targets, the Tip
// fields (bulbs on the gable, which end is bulb 0, bulbs per row), add, then take it out.
// usage: NODE_PATH=$(npm root -g) node tests/ui_tip.js http://127.0.0.1:PORT/ [sheet.png] [card.png]
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2];
  const out = { errors: [], overflow: [], small: [], steps: [] };
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, hasTouch: true, isMobile: true });
    page.on('pageerror', e => out.errors.push('pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) out.errors.push('console: ' + m.text()) });
    page.on('dialog', d => { out.dialogs = (out.dialogs || []).concat(d.message().slice(0, 60)); d.accept() });
    await page.addInitScript(() => { try { localStorage.setItem('setupSkip', '1') } catch (e) {} });
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
    const box = page.locator('#frBoxes .frb', { hasText: 'Tip' }).first();
    await box.locator('button[data-a="check"]').click();
    await box.locator('.frv').waitFor({ timeout: 20000 });
    out.verdict = await box.locator('.frv').textContent();
    out.steps.push('check');
    const yes = box.locator('button[data-y="1"]');
    if (await yes.count()) { await yes.click(); out.saidGreen = true }
    await box.locator('button[data-a="add"]').click();
    await page.waitForSelector('#sheet.on #frGo');
    out.tipHiddenFirst = await page.evaluate(() => document.querySelector('#frTip').hidden);
    await page.click('#sheet .chip:has-text("Top of the Peak")');
    out.steps.push('top of the peak');
    out.tipHidden = await page.evaluate(() => document.querySelector('#frTip').hidden);
    out.joinHidden = await page.evaluate(() => document.querySelector('#frJoinBox').hidden);
    out.name = await page.inputValue('#frName');
    out.visible = await page.inputValue('#frVisible');
    out.rows = await page.inputValue('#frRows');
    out.rowsHelp = await page.textContent('#frRowsHelp');
    out.joins = await page.textContent('#frTipJoins');
    out.flip = await page.evaluate(() => [...document.querySelectorAll('#frFlip .chip')].map(c => [c.textContent, c.classList.contains('on')]));
    await page.fill('#frCount', '108');
    await width('tip sheet');
    await touch('#sheet .fr button', 'tip sheet');
    out.inputH = await page.evaluate(() => ['#frVisible', '#frRows'].map(s => Math.round(document.querySelector(s).getBoundingClientRect().height)));
    if (process.argv[3]) { await page.locator('#sheet').screenshot({ path: process.argv[3] }).catch(() => {}) }
    // flip and back, change the rows (the help follows), then put them back
    await page.click('#frFlip .chip:has-text("The other end")');
    out.flipped = await page.evaluate(() => document.querySelector('#frFlip .chip.on').textContent);
    await page.click('#frFlip .chip:has-text("top row ends")');
    await page.fill('#frRows', '6, 5, 1');
    out.rowsHelp2 = await page.textContent('#frRowsHelp');
    await page.fill('#frRows', '7, 4, 1');
    await page.click('#frGo');
    await page.waitForSelector('#frBoxes .frb:has-text("Tip") button[data-a="blink"]', { timeout: 10000 });
    out.steps.push('added');
    out.cardText = await page.locator('#frBoxes .frb', { hasText: 'Tip' }).first().textContent();
    await width('after add');
    if (process.argv[4]) { await page.locator('#fourthCard').screenshot({ path: process.argv[4] }).catch(() => {}) }
    await page.locator('#frBoxes .frb', { hasText: 'Tip' }).first().locator('button[data-a="remove"]').click();
    await page.waitForSelector('#frBoxes .frb:has-text("Not in the show yet")', { timeout: 10000 });
    out.steps.push('taken out');
  } catch (e) {
    out.errors.push('flow: ' + e.message.split('\n')[0]);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
