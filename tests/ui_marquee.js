// Playwright check of the marquee end to end on an iPhone-sized screen (run by
// tests/test_r15_marquee_ui.py against fake boxes from tools/fake_govee.py). The owner,
// round 3: "they all just flash the letter without a marquee".
//   1. Words on the Peak: pick Marquee; the preview scrolls; Play now and the HOUSE scrolls
//      (the engine's frames on /api/stream: the Peak's lit bulbs slide left).
//   2. Save as preset keeps the mode; its Edit screen shows a Single letter / Marquee
//      switch at the top with Marquee on, and only the marquee's settings below it.
//   3. The built-in "Go Broncos (scrolling)" plays as a marquee on the house; flipping the
//      switch in the editor to Single letter shows the single-letter settings instead.
// No JS errors, no horizontal scroll, 44 px touch targets.
// usage: NODE_PATH=$(npm root -g) node tests/ui_marquee.js http://127.0.0.1:PORT/ [shots dir]
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2], shots = process.argv[3] || '';
  const out = { errors: [], overflow: [], small: [], steps: [] };
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, hasTouch: true, isMobile: true });
    page.on('pageerror', e => out.errors.push('pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) out.errors.push('console: ' + m.text()) });
    page.on('dialog', d => d.accept('Marquee Test'));
    await page.addInitScript(() => { try { localStorage.setItem('setupSkip', '1') } catch (e) {} });
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForSelector('#txtCols .chip', { timeout: 8000 });
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
    // the Peak's lit bulbs in a frame (hex per bulb): their mean X, or null when dark
    const peakX = (hex) => {
      const P = LAYOUT.pixels, idx = LAYOUT.runs_idx.Peak || [];
      let n = 0, sx = 0;
      for (const i of idx) { const c = hex.substr(i * 6, 6); if (c && Math.max(parseInt(c.substr(0, 2), 16), parseInt(c.substr(2, 2), 16), parseInt(c.substr(4, 2), 16)) > 100) { n++; sx += P[i][0] } }
      return n ? sx / n : null;
    };
    // what the house shows: the engine's frames for `ms` milliseconds
    const house = (ms) => page.evaluate(([ms, f]) => new Promise(res => {
      const peakX = eval(f), xs = [], es = new EventSource('/api/stream'), seen = new Set();
      es.onmessage = e => { seen.add(e.data); xs.push(peakX(e.data)) };
      setTimeout(() => { es.close(); res({ frames: seen.size, xs }) }, ms);
    }), [ms, '(' + peakX.toString() + ')']);
    const slides = (xs) => { let left = 0; for (let k = 1; k < xs.length; k++) if (xs[k] != null && xs[k - 1] != null && xs[k] < xs[k - 1] - 0.3) left++; return left };

    // 1. Words on the Peak, Marquee
    await page.locator('#txtCard').scrollIntoViewIfNeeded();
    await page.fill('#txtIn', 'GO BRONCOS');
    await page.click('#txtMode button[data-v="marquee"]');
    out.modeOn = await page.textContent('#txtMode button.on');
    await page.waitForFunction(() => TX.pv && TX.pv.frames.length > 30 && TX.mode === 'marquee', null, { timeout: 15000 });
    await page.waitForTimeout(800);
    out.preview = await page.evaluate(f => { const peakX = eval(f), fr = TX.pv.frames; return { distinct: new Set(fr).size, xs: fr.map(peakX) } }, '(' + peakX.toString() + ')');
    out.previewSlides = slides(out.preview.xs); delete out.preview.xs;
    await width('words card');
    await touch('#txtCard button', 'words card');
    if (shots) await page.locator('#txtCard').screenshot({ path: shots + '/words-marquee.png' }).catch(() => {});
    out.steps.push('preview');
    await page.click('#txtPlay');
    await page.waitForFunction(async () => { const s = await (await fetch('/api/status')).json(); return s.playing && /GO BRONCOS/i.test(s.playing.name) }, null, { timeout: 10000, polling: 250 });
    const h1 = await house(5000);
    out.houseFrames = h1.frames; out.houseSlides = slides(h1.xs);
    out.steps.push('house');

    // 2. Save as preset keeps the mode; the Edit screen shows the switch
    await page.click('#txtSave');
    await page.waitForTimeout(800);
    out.saved = await page.evaluate(async () => { const r = await (await fetch('/api/preset?id=marquee-test')).json(); return r.layers && r.layers[0].params.mode });
    await page.evaluate(() => editPreset('marquee-test'));
    await page.waitForSelector('#ptMode', { timeout: 8000 });
    const editor = async () => page.evaluate(() => ({
      on: (document.querySelector('#ptMode button.on') || {}).textContent || '',
      top: (() => { const s = document.querySelector('#ptMode'), r = s.getBoundingClientRect(); return Math.round(r.top + scrollY) })(),
      labels: [...document.querySelectorAll('.prm .lbl')].map(l => l.firstChild ? l.firstChild.textContent.trim() : ''),
    }));
    out.edit1 = await editor();
    await width('editor');
    await touch('#ptMode button', 'editor switch');
    if (shots) { await page.evaluate(() => document.querySelector('#ptMode').scrollIntoView({ block: 'start' })); await page.evaluate(() => scrollBy(0, -260)); await page.waitForTimeout(300); await page.screenshot({ path: shots + '/editor-switch-marquee.png' }).catch(() => {}) }
    out.steps.push('saved');

    // 3. The built-in scrolling preset: on the house, and the switch in its editor
    out.builtin = await page.evaluate(async () => (await (await fetch('/api/presets')).json()).filter(p => /-scroll$/.test(p.id)).map(p => p.id + '|' + p.name));
    await page.evaluate(() => tabTo('play'));                          // leave the editor (it plays its draft)
    await page.waitForTimeout(600);
    await page.evaluate(() => playPreset('text-go-broncos-scroll'));
    await page.waitForFunction(async () => { const s = await (await fetch('/api/status')).json(); return s.playing && /scrolling/.test(s.playing.name) }, null, { timeout: 10000, polling: 250 });
    const h2 = await house(6000);
    out.builtinFrames = h2.frames; out.builtinSlides = slides(h2.xs);
    out.playing = await page.evaluate(async () => { const s = await (await fetch('/api/status')).json(); return s.playing && s.playing.name });   // still it, all along
    await page.evaluate(() => editPreset('text-go-broncos-scroll'));
    await page.waitForFunction(() => { const b = document.querySelector('#ptMode button.on'); return b && b.dataset.v === 'marquee' }, null, { timeout: 8000 });
    // the editor opens on the first layer; pick the text layer (the second)
    out.edit2 = await editor();
    await page.click('#ptMode button[data-v="single"]');
    await page.waitForFunction(() => { const b = document.querySelector('#ptMode button.on'); return b && b.dataset.v === 'single' }, null, { timeout: 8000 });
    out.edit3 = await editor();
    out.draftMode = await page.evaluate(() => draft.layers.map(l => l.params && l.params.mode).filter(Boolean));
    if (shots) { await page.evaluate(() => document.querySelector('#ptMode').scrollIntoView({ block: 'start' })); await page.evaluate(() => scrollBy(0, -260)); await page.waitForTimeout(300); await page.screenshot({ path: shots + '/editor-switch-single.png' }).catch(() => {}) }
    await width('editor single');
    out.steps.push('editor');
  } catch (e) {
    out.errors.push('flow: ' + e.message.split('\n')[0]);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
