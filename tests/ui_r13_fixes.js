// Two phones on one controller (run by tests/test_r13_ui_fixes.py):
// 1. phone A starts Match the runs; phone B, only open on Play, changes tab and goes away:
//    A's test keeps running.
// 2. phone A walks the bulb check; phone B (never opened More) taps Open on the banner: the
//    walk shows at once, not "Done". While the check runs the colour test swatches are off.
// 3. More -> Fourth run -> "Is it answering now?", peek at Shows and come back: the answer shows.
// usage: NODE_PATH=$(npm root -g) node tests/ui_r13_fixes.js URL
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2];
  const out = { errors: [] };
  const browser = await chromium.launch();
  const st = async () => (await (await fetch(url + 'api/status')).json());
  const mk = async (tab) => {
    const c = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    await c.addInitScript(t => { try { localStorage.setItem('setupSkip', '1'); if (t) localStorage.setItem('tab', t) } catch (e) {} }, tab);
    const p = await c.newPage();
    p.on('pageerror', e => out.errors.push('pageerror: ' + e.message));
    p.on('dialog', d => d.accept());
    return p;
  };
  try {
    // ---------------------------------------------------------- 1. Match the runs, two phones
    const A = await mk('play'), B = await mk('play');
    await A.goto(url); await B.goto(url); await A.waitForTimeout(800);
    await A.click('#nav button[data-t="more"]'); await A.waitForSelector('#mtSw button[data-k="red"]');
    await A.click('#mtSw button[data-k="red"]'); await A.waitForTimeout(1200);
    out.matchAfterA = (await st()).match;
    await B.waitForTimeout(3000);                                     // B's status polls see the test
    await B.click('#nav button[data-t="shows"]'); await B.waitForTimeout(800);
    out.matchAfterBTab = (await st()).match;
    await B.goto('about:blank').catch(() => {}); await A.waitForTimeout(1200);   // pagehide on B
    out.matchAfterBLeave = (await st()).match;
    out.aNote = (await A.textContent('#mtNote')).trim();
    await A.click('#nav button[data-t="play"]'); await A.waitForTimeout(800);   // A leaving More ends its own test
    out.matchAfterAStop = (await st()).match;

    // ---------------------------------------------------------- 2. bulb check banner on phone B
    const B2 = await mk('play');
    await B2.goto(url); await B2.waitForTimeout(500);
    await A.click('#nav button[data-t="more"]'); await A.waitForSelector('#bcRuns .bcr');
    await A.click('#bcRuns .bcr:first-child button[data-a="walk"]'); await A.waitForSelector('#bcWalk .big');
    await A.click('#bcWalk button[data-a="next"]'); await A.waitForTimeout(400);
    await B2.waitForFunction(() => /bulb check is on/.test(document.getElementById('banners').textContent), null, { timeout: 10000 });
    await B2.click('#banners button:has-text("Open")'); await B2.waitForTimeout(400);
    out.bOpen = (await B2.textContent('#bcWalk')).replace(/\s+/g, ' ');
    await A.evaluate(() => closeSheet()); await A.waitForTimeout(2500);            // A peeks at the colour test card
    out.swDisabled = await A.evaluate(() => [...document.querySelectorAll('#mtSw button')].every(b => b.disabled));
    await A.click('#banners button:has-text("Done")'); await A.waitForTimeout(2500);
    out.swDisabledAfter = await A.evaluate(() => [...document.querySelectorAll('#mtSw button')].some(b => b.disabled));

    // ---------------------------------------------------------- 3. fourth run check, peek away
    const F = await mk('play');
    let released = 0;
    const box = { mac: 'tip-box', name: 'Tip box', sku: 'H705E', ip: '10.0.0.99', in_show: false };
    const json = (route, o) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(o) });
    await F.route('**/api/fourth', r => json(r, { boxes: [box], cloud: {}, locations: [] }));
    await F.route('**/api/fourth/check', async r => { await new Promise(z => setTimeout(z, 3000));
      json(r, { ok: true, status: '3/3', verdict: 'It answers on the LAN.',
        steps: [{ step: 'Discovery scan', ok: true, say: 'answered' }, { step: 'Status command', ok: true, say: '3 of 3 answered' }] }) });
    await F.route('**/api/fourth/answer', r => json(r, { ok: true, say: 'Good: it answers.' }));
    await F.route('**/api/fourth/release', r => { released++; json(r, { ok: true }) });
    await F.goto(url); await F.waitForTimeout(600);
    await F.click('#nav button[data-t="more"]'); await F.waitForSelector('#frBoxes button[data-a="check"]');
    await F.click('#frBoxes button[data-a="check"]'); await F.waitForTimeout(500);
    await F.click('#nav button[data-t="shows"]'); await F.waitForTimeout(500);
    await F.click('#nav button[data-t="more"]'); await F.waitForTimeout(300);
    out.frBtnBack = (await F.textContent('#frBoxes button[data-a="check"]')).trim();
    await F.waitForTimeout(3500);
    out.frOut = (await F.textContent('#frBoxes .fr-out')).replace(/\s+/g, ' ');
    await F.click('#nav button[data-t="shows"]'); await F.waitForTimeout(300);
    await F.click('#nav button[data-t="more"]'); await F.waitForTimeout(300);   // still there after another peek
    await F.click('#frBoxes .fr-out [data-y="1"]'); await F.waitForTimeout(500);
    out.frAnswered = (await F.textContent('#frBoxes .fr-out .frv')).trim();
    out.frReleased = released;
  } catch (e) {
    out.errors.push('script: ' + e.message);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
