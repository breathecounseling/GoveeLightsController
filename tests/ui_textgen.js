// Playwright check of Play -> "Words on the Peak" on an iPhone-sized screen (run by
// tests/test_r13_textgen_ui.py against fake boxes from tools/fake_govee.py): type a phrase,
// the preview draws the Peak, Single letter / Marquee, a colour chip, Play now, Save as
// preset (named), and the "Colours shifted" chip with its one-tap Reset. No JS errors, no
// horizontal scroll, 44 px touch targets.
// usage: NODE_PATH=$(npm root -g) node tests/ui_textgen.js http://127.0.0.1:PORT/ [card.png] [marquee.png]
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2];
  const out = { errors: [], overflow: [], small: [], steps: [] };
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, hasTouch: true, isMobile: true });
    page.on('pageerror', e => out.errors.push('pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) out.errors.push('console: ' + m.text()) });
    page.on('dialog', d => { out.dialog = d.message(); out.dialogDefault = d.defaultValue(); d.accept('Go Broncos Text') });
    await page.addInitScript(() => { try { localStorage.setItem('setupSkip', '1') } catch (e) {} });
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForSelector('#txtCols .chip', { timeout: 8000 });       // the card is set up
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
    const litPx = () => page.evaluate(() => { const c = document.querySelector('#txtPv'), x = c.getContext('2d');
      const d = x.getImageData(0, 0, c.width, c.height).data; let n = 0;
      for (let i = 0; i < d.length; i += 4) if (d[i] > 120 || d[i + 1] > 120 || d[i + 2] > 120) n++; return n });
    await page.locator('#txtCard').scrollIntoViewIfNeeded();
    await page.fill('#txtIn', 'go broncos~');
    await page.waitForFunction(() => TX.pv && TX.pv.frames.length > 10, null, { timeout: 15000 });
    await page.waitForTimeout(1200);
    out.lit = await litPx();
    out.skip = await page.textContent('#txtSkip');
    out.steps.push('preview');
    out.modeOn = await page.textContent('#txtMode button.on');
    out.label1 = await page.textContent('#txtSpdL');
    await page.click('#txtCols .chip:has-text("Broncos")');
    out.chipOn = await page.textContent('#txtCols .chip.on');
    await page.waitForTimeout(1500);                                     // the new colours' preview
    await width('card');
    await touch('#txtCard button', 'card');
    out.inputH = await page.evaluate(() => Math.round(document.querySelector('#txtIn').getBoundingClientRect().height));
    if (process.argv[3]) { await page.locator('#txtCard').screenshot({ path: process.argv[3] }).catch(() => {}) }
    await page.click('#txtMode button[data-v="marquee"]');
    out.label2 = await page.textContent('#txtSpdL');
    await page.waitForFunction(() => TX.pv && TX.pv.frames.length > 10, null, { timeout: 15000 });
    await page.waitForTimeout(2500);
    if (process.argv[4]) { await page.locator('#txtCard').screenshot({ path: process.argv[4] }).catch(() => {}) }
    out.steps.push('marquee');
    await page.click('#txtPlay');
    await page.waitForTimeout(600);
    out.playing = await page.evaluate(async () => { const s = await (await fetch('/api/status')).json(); return s.playing && s.playing.name });
    out.steps.push('played');
    await page.click('#txtSave');
    await page.waitForTimeout(800);
    out.presets = await page.evaluate(async () => (await (await fetch('/api/presets')).json()).map(p => p.id));
    out.steps.push('saved');
    // colours shifted: the hue knob off zero shows the chip; one tap resets it
    out.chipHidden0 = await page.evaluate(() => document.querySelector('#hueChip').hidden);
    await page.evaluate(() => fetch('/api/master', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'lights' }, body: JSON.stringify({ hue: 0.08 }) }));
    await page.evaluate(() => refresh());
    await page.waitForFunction(() => !document.querySelector('#hueChip').hidden, null, { timeout: 6000 });
    out.chipText = await page.textContent('#hueChip');
    await touch('#hueChip button', 'hue chip');
    await page.click('#hueReset');
    await page.waitForFunction(() => document.querySelector('#hueChip').hidden, null, { timeout: 6000 });
    out.hue = await page.evaluate(async () => (await (await fetch('/api/status')).json()).master.hue);
    out.steps.push('hue reset');
    await width('end');
  } catch (e) {
    out.errors.push('flow: ' + e.message.split('\n')[0]);
    out.debug = await browser.contexts()[0].pages()[0].evaluate(() => { try { return {seq: TX.seq, pv: TX.pv && TX.pv.frames.length, v: document.querySelector('#txtIn').value} } catch (e) { return String(e) } }).catch(e => String(e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
