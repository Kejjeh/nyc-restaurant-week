/* Every count the site prints beside a control is a promise about what
 * clicking it gives you. This checks that the promise is kept.
 *
 *   node tools/verify_ui_counts.mjs [--port 8899] [--keep-open]
 *
 * It serves docs/ itself, drives a headless Chromium over both pages, and for
 * every facet chip, every chart bar and every roster facet value compares the
 * number printed on the control against the number of rows it actually
 * delivers. Exit code 1 on any mismatch, with the offenders named.
 *
 * Why a tool and not a test: there is no JS harness in this repo and CI has no
 * browser, so this is not wired into `checks.yml`. It exists because the audit
 * that found the roster counting every facet against the whole roster --
 * "Manhattan 1104" while filtered to 62 Michelin-starred venues, and clicking
 * "Queens 55" returning nothing -- had to be rebuilt by hand each time. Run it
 * after touching app.js, venues.js, or anything that feeds a count into either.
 *
 * Playwright is not a dependency of this project. Point at an existing install
 * with PLAYWRIGHT_MODULE and, if the browser is not where Playwright expects,
 * CHROMIUM_PATH:
 *
 *   PLAYWRIGHT_MODULE=/usr/lib/node_modules/playwright/index.js \
 *   CHROMIUM_PATH=/opt/pw-browsers/chromium-1194/chrome-linux/chrome \
 *   node tools/verify_ui_counts.mjs
 */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(fileURLToPath(new URL('.', import.meta.url)), '..');
const DOCS = join(ROOT, 'docs');
const PORT = Number(argOf('--port') || 8899);
const KEEP = process.argv.includes('--keep-open');

function argOf(flag) {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : null;
}

const TYPES = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
};

/* Serves docs/ and nothing above it. */
function serve() {
  return new Promise((ok) => {
    const s = createServer(async (req, res) => {
      const path = normalize(decodeURIComponent(req.url.split('?')[0]))
        .replace(/^(\.\.[/\\])+/, '');
      const file = join(DOCS, path === '/' ? 'index.html' : path);
      if (!file.startsWith(DOCS)) { res.writeHead(403).end(); return; }
      try {
        const body = await readFile(file);
        res.writeHead(200, { 'Content-Type': TYPES[extname(file)] || 'application/octet-stream' });
        res.end(body);
      } catch { res.writeHead(404).end('not found'); }
    });
    s.listen(PORT, () => ok(s));
  });
}

async function chromium() {
  const mod = process.env.PLAYWRIGHT_MODULE || 'playwright';
  let pw;
  try {
    pw = await import(mod);
  } catch {
    console.error(
      `Could not import Playwright from ${mod}.\n`
      + 'Set PLAYWRIGHT_MODULE to an existing install, e.g.\n'
      + '  PLAYWRIGHT_MODULE=/usr/lib/node_modules/playwright/index.js node tools/verify_ui_counts.mjs');
    process.exit(2);
  }
  const c = (pw.default || pw).chromium;
  const opts = { args: ['--no-sandbox'] };
  if (process.env.CHROMIUM_PATH) opts.executablePath = process.env.CHROMIUM_PATH;
  return c.launch(opts);
}

const failures = [];
const note = (page, control, printed, delivered) => {
  failures.push(`${page}: "${control}" prints ${printed}, delivers ${delivered}`);
};

/* The dashboard: facet chips, then the clickable bars on each stats chart. */
async function dashboard(browser, base) {
  const url = `${base}/restaurant-week.html`;
  const page = await browser.newPage();
  const shown = () => page.$eval('#shown', (n) => Number(n.textContent.replace(/,/g, '')));

  await page.goto(url, { waitUntil: 'networkidle' });
  await page.click('#filterBtn');
  await page.waitForTimeout(400);
  const chips = await page.$$eval('#facets .chip',
    (cs) => cs.filter((c) => c.dataset.facet && c.querySelector('.c'))
      .map((c) => [c.dataset.facet, c.dataset.value,
                   Number(c.querySelector('.c').textContent.replace(/,/g, ''))]));
  console.log(`dashboard facets: ${chips.length} chips with a printed count`);
  for (const [facet, value, printed] of chips) {
    const toggle = ([f, v]) => {
      const c = [...document.querySelectorAll('#facets .chip')]
        .find((x) => x.dataset.facet === f && x.dataset.value === v);
      if (c) c.click();
    };
    await page.evaluate(toggle, [facet, value]);
    await page.waitForTimeout(70);
    const got = await shown();
    if (got !== printed) note('dashboard', `${facet}=${value}`, printed, got);
    await page.evaluate(toggle, [facet, value]);
    await page.waitForTimeout(50);
  }

  /* Chart bars carry their count in the aria-label, either "Name · N" or
     "N close <date>". Clicking one clears the other filters, so each bar is
     checked from a fresh load. */
  const CHARTS = ['chartClose', 'chartBasis', 'chartBorough', 'chartCuisine'];
  for (const id of CHARTS) {
    const sel = `#${id} .plot [aria-label]`;
    await page.goto(`${url}#view=stats`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(500);
    const labels = await page.$$eval(sel, (ns) => ns.map((n) => n.getAttribute('aria-label')));
    console.log(`${id}: ${labels.length} clickable bars`);
    for (const label of labels) {
      const m = /·\s*(\d[\d,]*)\s*$/.exec(label) || /^(\d[\d,]*)\s+close\b/.exec(label);
      if (!m) continue;
      await page.goto(`${url}#view=stats`, { waitUntil: 'networkidle' });
      await page.waitForTimeout(420);
      /* Dispatched rather than clicked at a coordinate: these are overlapping
         SVG rects and a positional click lands on the neighbour. */
      await page.evaluate(([s, want]) => {
        const n = [...document.querySelectorAll(s)]
          .find((x) => x.getAttribute('aria-label') === want);
        if (n) n.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      }, [sel, label]);
      await page.waitForTimeout(420);
      const got = await shown();
      const printed = Number(m[1].replace(/,/g, ''));
      if (got !== printed) note(id, label, printed, got);
    }
  }
  await page.close();
}

/* The roster: every value in every facet group, twice over.
 *
 * The clean-page pass alone proves nothing about the bug this tool exists for.
 * With no filter applied, "count against every row" and "count against the
 * rows surviving every OTHER facet" are the same arithmetic -- there are no
 * other facets. The roster shipped for weeks counting against the whole roster
 * and a clean-page check would have called it green. The bug only appears once
 * something is already selected, which is what the paired pass does.
 */
async function roster(browser, base) {
  const page = await browser.newPage();
  const url = `${base}/index.html`;

  const values = (gi) => page.$$eval('.facet', (gs, i) =>
    (gs[i] ? [...gs[i].querySelectorAll('.facetOpt')].map((o) => [
      o.querySelector('input').id,
      o.querySelector('.facetName').textContent,
      Number(o.querySelector('.facetN').textContent.replace(/,/g, '')),
    ]) : []), gi).catch(() => []);

  /* Rows the page would show: its own filter predicate over its own rows, so
     this measures delivery rather than re-deriving the count from the same
     formula that printed it. */
  const delivered = () => page.evaluate(
    () => STATE.rows.filter((v) => matches(v)).length);

  const tick = (id) => page.evaluate((cid) => {
    /* The checkbox is visually hidden behind its label, so .check() times out
       waiting for it to be visible. Clicking it fires the same handler. */
    const n = document.getElementById(cid);
    if (n) n.click();
    return Boolean(n);
  }, id);

  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForTimeout(500);
  const groups = await page.$$eval('.facet', (gs) => gs.map((g) => g.querySelector('h3').textContent));
  console.log(`roster facets: ${groups.length} groups`);

  // pass 1 -- nothing else selected
  for (let g = 0; g < groups.length; g++) {
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.waitForTimeout(350);
    for (const [id, name, printed] of await values(g)) {
      if (!printed) continue;
      await page.goto(url, { waitUntil: 'networkidle' });
      await page.waitForTimeout(300);
      if (!(await tick(id))) continue;
      await page.waitForTimeout(250);
      const got = await delivered();
      if (got !== printed) note('roster', `${groups[g]} / ${name}`, printed, got);
    }
  }

  // pass 2 -- one filter already applied, which is the case that matters
  const PARTNER_VALUES = 2;
  for (let a = 0; a < groups.length; a++) {
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.waitForTimeout(350);
    const first = (await values(a)).find(([, , n]) => n > 0);
    if (!first) continue;
    for (let bIdx = 0; bIdx < groups.length; bIdx++) {
      if (bIdx === a) continue;
      await page.goto(url, { waitUntil: 'networkidle' });
      await page.waitForTimeout(300);
      if (!(await tick(first[0]))) continue;
      await page.waitForTimeout(250);
      /* Group order can change once a filter drops a group to one value, so
         find the partner by its heading rather than by index. */
      const bNow = (await page.$$eval('.facet', (gs) =>
        gs.map((g) => g.querySelector('h3').textContent))).indexOf(groups[bIdx]);
      if (bNow < 0) continue;
      for (const [id, name, printed] of (await values(bNow)).slice(0, PARTNER_VALUES)) {
        if (!printed) continue;
        if (!(await tick(id))) continue;
        await page.waitForTimeout(220);
        const got = await delivered();
        if (got !== printed) {
          note('roster', `${groups[bIdx]} / ${name} (with ${groups[a]} / ${first[1]} on)`,
               printed, got);
        }
        await tick(id);
        await page.waitForTimeout(150);
      }
    }
  }
  await page.close();
}

const server = await serve();
const base = `http://localhost:${PORT}`;
const browser = await chromium();
try {
  await dashboard(browser, base);
  await roster(browser, base);
} finally {
  if (!KEEP) { await browser.close(); server.close(); }
}

if (failures.length) {
  console.error(`\n${failures.length} control(s) do not deliver what they print:`);
  failures.forEach((f) => console.error('  ' + f));
  process.exit(1);
}
console.log('\nEvery control delivers exactly the count it prints.');
