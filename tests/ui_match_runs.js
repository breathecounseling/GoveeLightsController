// Playwright check of More -> Match the runs at phone width (run by tests/test_r12_calibration.py):
// no JS errors, no horizontal scroll at 390 px, 44 px touch targets, the colour / nudge / slider /
// stop / reset flow. usage: NODE_PATH=$(npm root -g) node tests/ui_match_runs.js URL [shot] [shot2]
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2];
  const out = { errors: [], overflow: [], small: [], steps: [] };
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true });
    page.on('pageerror', e => out.errors.push('pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) out.errors.push('console: ' + m.text()) });
    page.on('dialog', d => d.accept());
    await page.addInitScript(() => { try { localStorage.setItem('setupSkip', '1') } catch (e) {} });
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForTimeout(800);
    await page.click('#nav button[data-t="more"]');
    await page.waitForSelector('#mtRuns .mtr', { timeout: 8000 });
    out.steps.push('card');
    const api = async p => page.evaluate(async p => (await fetch('/api/' + p)).json(), p);
    const width = async where => {
      const w = await page.evaluate(() => [document.documentElement.scrollWidth, document.body.scrollWidth, innerWidth]);
      if (w[0] > w[2] || w[1] > w[2]) out.overflow.push(`${where}: ${w.join('/')}`);
    };
    const touch = async where => {
      const small = await page.evaluate(() => [...document.querySelectorAll('#matchCard button, #matchCard summary')]
        .filter(b => b.offsetParent !== null)
        .map(b => [b.textContent.trim().slice(0, 30), Math.round(b.getBoundingClientRect().height), Math.round(b.getBoundingClientRect().width)])
        .filter(x => x[1] < 44 || x[2] < 44));
      small.forEach(x => out.small.push(`${where}: "${x[0]}" ${x[1]}x${x[2]}`));
    };
    out.runs = await page.$$eval('#mtRuns .mtr b', b => b.map(x => x.textContent));
    out.stopHidden = await page.evaluate(() => getComputedStyle(document.querySelector('#mtStop')).display === 'none');
    await width('card');
    await touch('card');
    await page.click('#mtSw button[data-k="warm"]');
    await page.waitForSelector('#mtSw button.on[data-k="warm"]');
    out.match1 = (await api('status')).match;
    out.note = await page.textContent('#mtNote');
    out.steps.push('colour');
    await page.click('#mtRuns .mtr:nth-child(2) button[data-n="warmer"]');
    await page.waitForFunction(() => /warmer/.test(document.querySelector('#mtRuns .mtr:nth-child(2) small').textContent));
    out.words = await page.textContent('#mtRuns .mtr:nth-child(2) small');
    out.steps.push('warmer');
    await page.click('#mtRuns .mtr:nth-child(1) button[data-n="brighter"]');          // already full: the others dim
    await page.waitForFunction(() => /95% bright/.test(document.querySelector('#mtRuns .mtr:nth-child(3) small').textContent));
    out.steps.push('brighter');
    await page.click('#mtSw button[data-k="red"]');
    await page.waitForSelector('#mtSw button.on[data-k="red"]');
    out.match2 = (await api('status')).match;
    out.bar = await page.$$eval('#mtBar span', s => s.map(x => getComputedStyle(x).backgroundColor));
    await page.click('#mtRuns .mtr:nth-child(3) summary');
    const fade = page.locator('#mtRuns .mtr:nth-child(3) input[data-f="fade"]');
    await fade.evaluate(i => { i.value = '0.15'; i.dispatchEvent(new Event('input', { bubbles: true })) });
    await page.waitForTimeout(700);
    out.fadeOut = await page.textContent('#mtRuns .mtr:nth-child(3) input[data-f="fade"] + output');
    out.steps.push('slider');
    await width('fine-tune');
    await touch('fine-tune');
    if (process.argv[3]) await page.locator('#matchCard').screenshot({ path: process.argv[3] }).catch(() => {});
    out.cal = (await api('status')).runs.map(r => r.cal);
    await page.click('#mtStop');
    await page.waitForTimeout(300);
    out.match3 = (await api('status')).match;
    out.steps.push('stop');
    await page.click('#matchCard button:has-text("Reset all runs")');
    await page.waitForFunction(() => [...document.querySelectorAll('#mtRuns small')].every(s => /as it came/.test(s.textContent)));
    out.calAfter = (await api('status')).runs.map(r => r.cal);
    out.steps.push('reset');
    if (process.argv[4]) await page.locator('#matchCard').screenshot({ path: process.argv[4] }).catch(() => {});
    // leaving More ends a running test
    await page.click('#mtSw button[data-k="cool"]');
    await page.waitForSelector('#mtSw button.on[data-k="cool"]');
    await page.click('#nav button[data-t="play"]');
    for (let i = 0; i < 30; i++) {                     // (the start may still be on its way: it is stopped when it lands)
      await page.waitForTimeout(100);
      out.match4 = (await api('status')).match;
      if (out.match4 === null) break;
    }
    out.steps.push('leave');
  } catch (e) {
    out.errors.push('flow: ' + e.message);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
