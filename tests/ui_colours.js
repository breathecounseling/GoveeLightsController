// Playwright check of More -> Match the runs -> "Colours look wrong?" on an iPhone-sized
// screen (run by tests/test_r13_colour_ui.py against fake boxes from tools/fake_govee.py):
// no JS errors, no horizontal scroll, 44 px touch targets; answers the two questions (and
// the optional third) the way boxes wired as argv[3] would look, sees the result and the
// before/after, ends the test, sets one order by hand, and moves a "Somewhere else" Tip to
// the top of the Peak from the Fourth run card.
// usage: NODE_PATH=$(npm root -g) node tests/ui_colours.js URL '{"Peak":"RBG"}' [shot-dir]
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2], wiring = JSON.parse(process.argv[3] || '{}'), shots = process.argv[4] || '';
  const out = { errors: [], overflow: [], small: [], steps: [] };
  const browser = await chromium.launch();
  const shot = async (loc, name) => { if (shots) await loc.screenshot({ path: `${shots}/${name}.png` }).catch(() => {}) };
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, hasTouch: true, isMobile: true });
    page.on('pageerror', e => out.errors.push('pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) out.errors.push('console: ' + m.text()) });
    page.on('dialog', d => d.accept());
    page.on('response', async r => { if (r.status() >= 400) out.errors.push(`http ${r.status()} ${r.url().replace(/^.*\/api\//, '')}: ` + (await r.text().catch(() => '')).slice(0, 160)) });
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
    await page.click('#nav button[data-t="more"]');
    await page.waitForSelector('#ctGo', { state: 'visible' });
    await page.click('#ctSet summary');
    await page.waitForSelector('#ctOrders select');
    out.ordersBefore = await page.evaluate(() => [...document.querySelectorAll('#ctOrders select')].map(s => s.value));
    await width('card');
    await touch('#ctCard button, #ctCard select', 'card');
    await page.locator('#ctGo').scrollIntoViewIfNeeded();
    await shot(page.locator('#matchCard'), 'colours-card');
    out.steps.push('card');

    // the test: two questions per run, then the optional third
    await page.click('#ctGo');
    await page.waitForSelector('#sheet.on #ctWiz .ctq');
    out.runs = await page.evaluate(() => [...document.querySelectorAll('#ctWiz .ctq .q')].map(q => q.textContent));
    const answer = async (k) => {
      const qs = page.locator('#ctWiz .ctq');
      const n = await qs.count();
      for (let i = 0; i < n; i++) {
        const q = qs.nth(i), name = (await q.locator('.q').textContent()).replace(/^What colour is (.*)\?$/, '$1');
        const o = wiring[name] || 'RGB', saw = { R: 'red', G: 'green', B: 'blue' }[o[k]];
        await q.locator(`button[data-saw="${saw}"]`).click();
        await page.waitForSelector(`#ctWiz .ctq:nth-of-type(${i + 1}) button.on[data-saw="${saw}"]`, { timeout: 5000 }).catch(() => {});
      }
    };
    await answer(0);
    await width('question 1');
    await touch('#ctWiz button', 'question 1');
    await shot(page.locator('#sheet'), 'colours-question');
    out.q1 = await page.textContent('#ctWiz .big');
    await page.click('#ctWiz [data-a="next"]');
    await page.waitForFunction(() => /Second/.test(document.querySelector('#ctWiz .big').textContent));
    out.steps.push('question 2');
    await answer(1);
    await page.click('#ctWiz [data-a="check"]');
    await page.waitForFunction(() => /Third/.test(document.querySelector('#ctWiz .big').textContent));
    await answer(2);
    out.steps.push('check');
    await page.click('#ctWiz [data-a="finish"]');
    await page.waitForSelector('#ctWiz .ba');
    out.steps.push('result');
    out.result = await page.evaluate(() => [...document.querySelectorAll('#ctWiz .res')].map(p => p.textContent));
    await width('result');
    await touch('#ctWiz button', 'result');
    await shot(page.locator('#sheet'), 'colours-result');
    await page.click('#ctWiz [data-w="before"]');
    await page.waitForSelector('#ctWiz [data-w="before"].on');
    out.beforeOn = true;
    await page.waitForTimeout(700);                    // the house shows it (the test reads the frames)
    await page.click('#ctWiz [data-w="after"]');
    await page.waitForSelector('#ctWiz [data-w="after"].on');
    await page.waitForTimeout(700);
    await page.click('#ctWiz [data-a="done"]');
    await page.waitForFunction(() => !document.querySelector('#sheet').classList.contains('on'));
    out.steps.push('done');
    await page.waitForFunction(() => [...document.querySelectorAll('#ctOrders select')].some(s => s.value !== 'RGB'), null, { timeout: 8000 });
    out.ordersAfter = await page.evaluate(() => [...document.querySelectorAll('#ctOrders select')].map(s => s.value));
    out.orderText = await page.textContent('#ctOrders');
    await shot(page.locator('#matchCard'), 'colours-card-after');
    // by hand: one run to G-R-B, then back
    await page.locator('#ctOrders select').last().selectOption('GRB');
    await page.waitForTimeout(600);
    out.manual = await page.evaluate(() => fetch('/api/status').then(r => r.json()).then(s => s.runs.map(r => r.color_order)));
    await page.locator('#ctOrders select').last().selectOption(out.ordersAfter[out.ordersAfter.length - 1]);
    await page.waitForTimeout(400);
    out.steps.push('manual');

    // Bulb check links to it
    await page.click('#bcToCt');
    await page.waitForTimeout(500);
    out.linkLands = await page.evaluate(() => { const r = document.querySelector('#ctCard').getBoundingClientRect(); return r.top >= 0 && r.top < innerHeight });

    // Fourth run: the Tip added the old way gets a one-tap move
    await page.waitForSelector('#frBoxes button[data-a="peaktop"]', { timeout: 8000 });
    const card = page.locator('#frBoxes .frb', { has: page.locator('button[data-a="peaktop"]') }).first();
    out.offerText = await card.textContent();
    await card.locator('button[data-a="peaktop"]').scrollIntoViewIfNeeded();
    await touch('#frBoxes button', 'fourth');
    await shot(page.locator('#fourthCard'), 'tip-offer');
    await card.locator('button[data-a="peaktop"]').click();
    await page.waitForFunction(() => /Top of the Peak/.test(document.querySelector('#frBoxes').textContent) &&
      !document.querySelector('#frBoxes button[data-a="peaktop"]'), null, { timeout: 10000 });
    out.movedText = await page.locator('#frBoxes .frb', { hasText: 'Tip' }).first().textContent();
    await width('fourth after');
    await shot(page.locator('#fourthCard'), 'tip-moved');
    out.steps.push('peak top');
  } catch (e) {
    out.errors.push('flow: ' + e.message.split('\n')[0]);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
