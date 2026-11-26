// Playwright check of More -> Match the runs with iPhone-style touch (run by tests/test_r12_calibration.py):
// range inputs never take focus (as on iOS Safari), a slider paused mid-drag is not replaced under the
// finger, a colour tap shows at once while the lights are still switching on, and the bar preview / label
// ink. usage: NODE_PATH=$(npm root -g) node tests/ui_match_drag.js URL
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2];
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 375, height: 667 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true });
    page.on('pageerror', e => out.errors.push('pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) out.errors.push('console: ' + m.text()) });
    page.on('dialog', d => d.accept());
    await page.addInitScript(() => {
      try { localStorage.setItem('setupSkip', '1') } catch (e) {}
      document.addEventListener('focusin', e => { if (e.target.type === 'range') e.target.blur() }, true);   // like iOS
    });
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForTimeout(800);
    await page.click('#nav button[data-t="more"]');
    await page.waitForSelector('#mtRuns .mtr', { timeout: 8000 });
    // lights off: the boxes take a while to come on; the tap must show straight away
    await page.click('#mtSw button[data-k="warm"]', { noWaitAfter: true });
    await page.waitForTimeout(150);
    out.litAtOnce = await page.evaluate(() => !!document.querySelector('#mtSw button.on[data-k="warm"]'));
    out.noteAtOnce = await page.textContent('#mtNote');
    await page.waitForFunction(() => /switched on for the test/.test(document.querySelector('#mtNote').textContent), null, { timeout: 8000 });
    await page.waitForFunction(() => document.querySelector('#hdNow').textContent === 'Colour test', null, { timeout: 8000 }).catch(() => {});
    out.header = await page.textContent('#hdNow');
    out.warmInk = await page.$eval('#mtBar span', s => getComputedStyle(s).color);
    out.peakPreview = await page.evaluate(() => mtRgb('#ffb46b', mtCal(ST.runs[1])));
    // drag Eaves brightness with a pause in the middle
    await page.click('#mtRuns .mtr:nth-child(1) summary');
    await page.evaluate(() => {
      const i = document.querySelector('#mtRuns .mtr:nth-child(1) input[data-f="brightness"]');
      window._slider = i;
      i.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'touch' }));
      i.value = '0.84'; i.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await page.waitForTimeout(700);                                   // the finger rests: the save goes out and comes back
    out.sameNode = await page.evaluate(() => window._slider.isConnected &&
      document.querySelector('#mtRuns .mtr:nth-child(1) input[data-f="brightness"]') === window._slider);
    await page.evaluate(() => { const i = window._slider; i.value = '0.56'; i.dispatchEvent(new Event('input', { bubbles: true })) });
    await page.waitForTimeout(1300);                                  // the poll comes round mid-drag too
    out.sameNode = out.sameNode && await page.evaluate(() => window._slider.isConnected);
    await page.evaluate(() => window._slider.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerType: 'touch' })));
    await page.waitForTimeout(900);
    out.sliderValue = await page.$eval('#mtRuns .mtr:nth-child(1) input[data-f="brightness"]', i => i.value);
    await page.click('#mtSw button[data-k="blue"]');
    await page.waitForFunction(() => ST.match && ST.match.color === 'blue', null, { timeout: 8000 });
    await page.waitForSelector('#mtSw button.on[data-k="blue"]');
    out.blueInk = await page.$eval('#mtBar span', s => getComputedStyle(s).color);
    const w = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
    if (w[0] > w[1]) out.errors.push('sideways scroll ' + w.join('/'));
  } catch (e) {
    out.errors.push('flow: ' + e.message);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
