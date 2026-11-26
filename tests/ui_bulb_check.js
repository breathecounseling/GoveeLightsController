// Playwright check of More -> Bulb check at phone width (run by tests/test_r12_bulb_check_ui.py):
// no JS errors, no horizontal scroll at 390 px, 44 px touch targets, and the walk, mark,
// colour test, re-hung and gap check flows.
// usage: NODE_PATH=$(npm root -g) node tests/ui_bulb_check.js http://127.0.0.1:PORT/ [shot dir]
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2], shots = process.argv[3] || '';
  const out = { errors: [], overflow: [], small: [], steps: [], texts: {} };
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true });
    page.on('pageerror', e => out.errors.push('pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) out.errors.push('console: ' + m.text()) });
    page.on('dialog', d => d.accept());
    await page.addInitScript(() => { try { localStorage.setItem('setupSkip', '1') } catch (e) {} });
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForTimeout(800);
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
    const shot = async (name, sel) => { if (shots) await page.evaluate(() => document.getElementById('toast').classList.remove('show')); if (shots) await page.locator(sel).screenshot({ path: `${shots}/${name}.png` }).catch(() => {}) };
    const big = () => page.textContent('#bcWalk .big');
    const waitBig = async (re) => page.waitForFunction(r => { const b = document.querySelector('#bcWalk .big'); return b && new RegExp(r).test(b.textContent) }, re.source, { timeout: 8000 });

    await page.click('#nav button[data-t="more"]');
    await page.waitForSelector('#bcRuns .bcr', { timeout: 8000 });
    out.steps.push('card');
    await page.click('#bcRuns .bcr details summary');
    await width('card');
    await touch('#bulbCard button, #bulbCard summary', 'card');
    await shot('card', '#bulbCard');

    // walk Eaves: groups of 10, then one bulb, mark it
    await page.click('#bcRuns .bcr:first-child button[data-a="walk"]');
    await waitBig(/bulbs 1–10/);
    out.steps.push('walk');
    await width('walk');
    await touch('#bcWalk button', 'walk');
    await page.click('#bcWalk button[data-a="next"]');
    await waitBig(/bulbs 11–20/);
    out.texts.group = await big();
    await shot('walk-groups', '#sheet');
    await page.click('#bcWalk button[data-a="find"]');
    await waitBig(/^Bulb 11$/);
    await shot('walk-single', '#sheet');
    await page.click('#bcWalk button[data-a="dark"]');
    await waitBig(/^Bulb 12$/);
    out.steps.push('marked');
    out.texts.banner = await page.evaluate(() => document.querySelector('#banners').textContent);
    await page.click('#bcWalk button[data-a="groups"]');
    await waitBig(/bulbs 11–20/);
    await page.click('#bcWalk button[data-a="hung"]');
    await page.waitForTimeout(300);
    await page.click('#bcWalk button[data-a="done"]');
    await page.waitForFunction(() => !document.querySelector('#sheet').classList.contains('on'));
    await page.waitForSelector('#bcRuns [data-dead]', { timeout: 8000 });
    out.texts.dead = await page.textContent('#bcRuns [data-dead]');
    out.texts.rehung = await page.textContent('#bcRuns .bcr:first-child .frb');
    out.steps.push('back');
    await width('marked');
    await touch('#bulbCard button, #bulbCard summary', 'marked');
    await page.waitForTimeout(400);
    await shot('card-marked', '#bulbCard');

    // colour test, all runs
    await page.click('#bulbCard button:has-text("Colour test: all runs")');
    await waitBig(/Red/);
    await page.click('#bcWalk .bcsw button[data-c="blue"]');
    await waitBig(/Blue/);
    await page.click('#bcWalk button[data-a="next"]');
    await waitBig(/White/);
    out.steps.push('sweep');
    await touch('#bcWalk button', 'sweep');
    await width('sweep');
    await shot('sweep', '#sheet');
    await page.click('#bcWalk button[data-a="done"]');
    await page.waitForFunction(() => !document.querySelector('#sheet').classList.contains('on'));

    // gap check of the re-hung part, then on to the camera
    await page.click('#bcRuns .bcr:first-child [data-gap]');
    await waitBig(/bulbs 11–20/);
    out.texts.gap = await page.textContent('#bcWalk');
    out.steps.push('gap');
    await touch('#bcWalk button', 'gap');
    await shot('gap', '#sheet');
    await page.click('#bcWalk button[data-a="remap"]');
    await page.waitForFunction(() => document.querySelector('#sheetTitle').textContent === 'Map with your camera');
    out.steps.push('remap');
    // the tab bar is a safe place: the check stopped for the camera
    out.texts.status = await page.evaluate(async () => (await (await fetch('/api/status')).json()).bulb_check);
    await page.evaluate(() => closeSheet());

    // a check left running shows a banner on every tab with Done in reach
    await page.click('#bcRuns .bcr:nth-child(2) button[data-a="walk"]');
    await waitBig(/bulbs 1–10/);
    await page.evaluate(() => closeSheet());
    await page.click('#nav button[data-t="play"]');
    await page.waitForFunction(() => /bulb check is on the lights/.test(document.querySelector('#banners').textContent), null, { timeout: 8000 });
    out.steps.push('banner');
    await page.click('#banners button:has-text("Done")');
    await page.waitForFunction(() => !/bulb check/.test(document.querySelector('#banners').textContent), null, { timeout: 8000 });
    out.steps.push('ended');
  } catch (e) {
    out.errors.push('flow: ' + e.message);
    if (shots) await browser.contexts()[0].pages()[0].screenshot({ path: `${shots}/failed.png` }).catch(() => {});
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
