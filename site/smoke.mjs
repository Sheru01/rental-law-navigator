/* Smoke checks for the built site.
 *
 *   npm i -D playwright && node site/smoke.mjs
 *
 * Exits non-zero if any check fails, so it can gate a push. Set CHROMIUM_PATH
 * if Playwright cannot find a browser itself; set SITE_URL to check a deployed
 * copy instead of the local build.
 */
import { chromium } from 'playwright';

const launch = process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {};
const url = process.env.SITE_URL ||
  new URL('./dist/index.html', import.meta.url).href;

const b = await chromium.launch(launch);
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const errs = [];
p.on('pageerror', e => errs.push('PAGEERROR: ' + e.message));
p.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE: ' + m.text()); });
await p.goto(url);
await p.waitForTimeout(900);

const checks = [];
const ok = (name, cond) => checks.push([name, !!cond]);

ok('cards render', await p.locator('.card').count() > 0);
ok('legal city resolved', !/not resolved/.test(await p.locator('#jurisBlock').innerText()));

await p.locator('.evlink').first().click(); await p.waitForTimeout(500);
ok('drawer opens', await p.locator('#drawer.open').count() === 1);
ok('span highlighted in source', await p.locator('.hl').count() === 1);
ok('focus on drawer heading', await p.evaluate(() => document.activeElement.id) === 'drawerTitle');
await p.keyboard.press('Escape'); await p.waitForTimeout(400);
ok('Esc closes, focus returns', await p.locator('#drawer.open').count() === 0 &&
   (await p.evaluate(() => document.activeElement.className)).includes('evlink'));

await p.locator('#dateB').click(); await p.waitForTimeout(400);
ok('date B renders diff banner', await p.locator('.diffbanner').count() === 1);
ok('footer tracks active date', (await p.locator('#footMeta').innerText()).includes('2027-07-01'));

await p.locator('#navDebt').click(); await p.waitForTimeout(400);
ok('debt ledger has rows', await p.locator('.ledger-row').count() > 0);

// every ledger row label is distinct
const labels = await p.locator('.ledger-row').allTextContents();
ok('ledger rows distinct', new Set(labels).size === labels.length);

ok('no page errors', errs.length === 0);
checks.forEach(([n, v]) => console.log((v ? 'PASS  ' : 'FAIL  ') + n));
if (errs.length) console.log('errors: ' + errs.join(' | '));
await b.close();
process.exit(checks.every(c => c[1]) ? 0 : 1);
