// Installable app on an iPhone-sized screen (run by tests/test_r14_remote_pwa.py): the manifest
// and icon links of the app and the login page resolve, the manifest names the app and its
// icons load as real pictures, More -> "Use it away from home" says how this phone is connected
// and links the guide, and nothing logs an error to the console.
// usage: NODE_PATH=$(npm root -g) node tests/ui_install.js URL [screenshot.png]
const { chromium, devices } = require('playwright');

(async () => {
  const url = process.argv[2], shot = process.argv[3] || '';
  const out = { errors: [] };
  const { defaultBrowserType, ...iphone } = devices['iPhone 13'];
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext(iphone);
    await ctx.addInitScript(() => { try { localStorage.setItem('setupSkip', '1'); localStorage.setItem('tab', 'play') } catch (e) {} });
    const page = await ctx.newPage();
    page.on('pageerror', e => out.errors.push('pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error') out.errors.push('console: ' + m.text()) });
    page.on('response', r => { if (r.status() >= 400) out.errors.push(`http ${r.status()}: ${r.url()}`) });
    const links = () => page.evaluate(async () => {
      const res = {};
      for (const l of document.querySelectorAll('link[rel=manifest], link[rel=icon], link[rel=apple-touch-icon]')) {
        const r = await fetch(l.href, { credentials: 'include' });
        res[l.rel + ' ' + new URL(l.href).pathname] = { status: r.status, type: r.headers.get('content-type'),
                                                          cache: r.headers.get('cache-control') };
        if (l.rel === 'manifest') res.manifest = await r.json();
      }
      return res;
    });
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForTimeout(800);
    out.app = await links();
    out.icons = await page.evaluate(async m => {           // every manifest icon decodes as a picture
      const sizes = {};
      for (const ic of m.icons) {
        const img = new Image(); img.src = ic.src;
        try { await img.decode(); sizes[ic.src] = [img.naturalWidth, img.naturalHeight] } catch (e) { sizes[ic.src] = 'broken' }
      }
      return sizes;
    }, out.app.manifest);
    await page.click('#nav button[data-t="more"]');
    await page.waitForFunction(() => /\S/.test(document.getElementById('awayStatus').textContent), null, { timeout: 8000 });
    await page.locator('#awayCard').scrollIntoViewIfNeeded();
    out.away = await page.evaluate(() => {
      const c = document.getElementById('awayCard'), a = c.querySelector('a');
      return { text: c.textContent, href: a && a.getAttribute('href'), right: c.getBoundingClientRect().right,
               width: innerWidth, scroll: document.documentElement.scrollWidth };
    });
    if (shot) await page.locator('#awayCard').screenshot({ path: shot });
    await page.click('#awayCard a');
    await page.waitForLoadState('load');
    out.guide = { path: new URL(page.url()).pathname, title: await page.title(),
                  text: (await page.textContent('h1')).slice(0, 200),
                  overflow: await page.evaluate(() => document.documentElement.scrollWidth - innerWidth) };
    await page.goto(url + 'login', { waitUntil: 'load' });
    out.login = await links();
    delete out.login.manifest;
  } catch (e) {
    out.errors.push('flow: ' + e.message);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
