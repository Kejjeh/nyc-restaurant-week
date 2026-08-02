/* NYC Restaurant Week — value dashboard
 *
 * Vanilla JS, no build step, no framework. Renders docs/data/restaurants.json.
 *
 * Two repo-specific traps this file is written around:
 *   1. One slug is "53". Plain objects coerce integer-like keys and REORDER
 *      them ahead of string keys, so every slug-keyed lookup here uses a Map.
 *   2. Names carry diacritics ("Café Boulud", "Ma•dé"). Search folds diacritics
 *      on both sides so "cafe boulud" matches "Café Boulud".
 */
'use strict';

const DATA_URL = 'data/restaurants.json';

/* ---------- helpers ----------------------------------------------------- */

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

/** Fold diacritics + case so "Café" and "cafe" compare equal. */
const fold = (s) =>
  (s == null ? '' : String(s))
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();

const todayISO = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

/** Format an ISO date as "Aug 16" without touching timezones. */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const fmtDate = (iso) => {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m ? `${MONTHS[+m[2] - 1]} ${+m[3]}` : iso;
};

const money = (n) => (n == null ? null : `$${Number(n).toLocaleString('en-US')}`);

/* ---------- state ------------------------------------------------------- */

let DATA = null;
let ROWS = [];                 // array of restaurant objects
const EXPANDED = new Set();    // slugs whose detail is open

const FILTERS = {
  borough: new Set(),
  neighborhood: new Set(),
  cuisine: new Set(),
  tier: new Set(),
  meal: new Set(),
  verdict: new Set(),
  grade: new Set(),
  recognition: new Set(),
  tag: new Set(),
  sunday: new Set(),      // 'yes' | 'no'
  basis: new Set(),       // 'verified' | 'estimate' | 'none'
  menu: new Set(),        // 'pdf' | 'image_only' | 'none'
  bookableBy: null,       // ISO date string
};
let SORT = 'gap_usd_desc';
let QUERY = '';
let BANNER = () => {};   // set during boot, once the banner nodes exist

/* Facet groups are declared once. Adding a facet = one entry here; the panel,
   the counts, the URL state and the clear-all all follow automatically. */
const FACETS = [
  { key: 'borough',     title: 'Borough',       values: (r) => r.borough ? [r.borough] : [] },
  { key: 'neighborhood',title: 'Neighborhood',  values: (r) => r.neighborhood ? [r.neighborhood] : [], scroll: true },
  { key: 'cuisine',     title: 'Cuisine',       values: (r) => r.cuisines || [], scroll: true },
  { key: 'tier',        title: 'Price tier',    values: (r) => r.price_tiers || [] },
  { key: 'meal',        title: 'Meal',          values: (r) => r.meal_periods || [] },
  { key: 'sunday',      title: 'Sunday',        values: (r) => (r.sunday === true ? ['yes'] : r.sunday === false ? ['no'] : []),
                                                labels: { yes: 'Serves Sunday', no: 'No Sunday' } },
  { key: 'verdict',     title: 'Verdict',       values: (r) => r.verdict ? [r.verdict] : [] },
  { key: 'grade',       title: 'Evidence grade',values: (r) => r.grade ? [r.grade] : [],
                                                labels: { A: 'A — own menu' } },
  { key: 'basis',       title: 'Gap basis',     values: (r) => [r.gap_basis || 'none'],
                                                labels: { verified: 'Verified', estimate: 'Estimate', none: 'No comparable' } },
  { key: 'recognition', title: 'Recognition',   values: (r) => [...new Set((r.recognition || []).map((x) => x.source))] },
  { key: 'tag',         title: 'Dish tags',     values: (r) => [...new Set((r.tags || []).map((t) => t.tag))] },
  { key: 'menu',        title: 'Menu PDF',      values: (r) => [r.menu_state || 'none'],
                                                labels: { pdf: 'Readable PDF', image_only: 'Image-only PDF', none: 'No menu published' } },
];

const RECOG_LABEL = { michelin: 'Michelin', james_beard: 'James Beard', nyt: 'NYT' };
const labelFor = (facet, v) => (facet.labels && facet.labels[v]) || RECOG_LABEL[v] || v;

/* ---------- filtering --------------------------------------------------- */

function matches(r) {
  if (QUERY) {
    if (!r._hay.includes(QUERY)) return false;
  }

  for (const f of FACETS) {
    const sel = FILTERS[f.key];
    if (!sel || sel.size === 0) continue;
    const vals = f.values(r);
    let hit = false;
    for (const v of vals) { if (sel.has(v)) { hit = true; break; } }
    if (!hit) return false;
  }

  if (FILTERS.bookableBy) {
    // Unknown end dates are KEPT (an unprinted window is not a closed one),
    // but they are visibly flagged in the row.
    if (r.end_date && r.end_date < FILTERS.bookableBy) return false;
  }

  return true;
}

/* nulls always sort last, in every direction */
const cmpNullLast = (a, b, dir) => {
  const an = a == null, bn = b == null;
  if (an && bn) return 0;
  if (an) return 1;
  if (bn) return -1;
  return a < b ? -dir : a > b ? dir : 0;
};

const SORTS = {
  gap_usd_desc: (a, b) => cmpNullLast(a.gap_usd, b.gap_usd, -1) || a._name.localeCompare(b._name),
  gap_pct_desc: (a, b) => cmpNullLast(a.gap_pct, b.gap_pct, -1) || a._name.localeCompare(b._name),
  price_asc:    (a, b) => cmpNullLast(a.rw_price, b.rw_price, 1) || a._name.localeCompare(b._name),
  price_desc:   (a, b) => cmpNullLast(a.rw_price, b.rw_price, -1) || a._name.localeCompare(b._name),
  end_asc:      (a, b) => cmpNullLast(a.end_date, b.end_date, 1) || a._name.localeCompare(b._name),
  end_desc:     (a, b) => cmpNullLast(a.end_date, b.end_date, -1) || a._name.localeCompare(b._name),
  name_asc:     (a, b) => a._name.localeCompare(b._name),
  rank_asc:     (a, b) => cmpNullLast(a.rank, b.rank, 1) || a._name.localeCompare(b._name),
};

/* ---------- rendering --------------------------------------------------- */

function pill(cls, text, title) {
  const p = el('span', `pill ${cls}`, text);
  if (title) p.title = title;
  return p;
}

function isUrgent(r) {
  return !!(DATA.book_by && r.end_date && r.end_date <= DATA.book_by);
}

function renderGapCell(r) {
  const cell = el('div', 'gapCell');
  if (r.gap_basis === 'estimate') cell.classList.add('est');

  if (r.gap_usd == null) {
    cell.append(el('div', 'gapNone', '—'));
    cell.append(el('div', 'gapLabel', 'no comparable'));
    return cell;
  }

  let usd = money(r.gap_usd);
  if (r.gap_usd_high != null && r.gap_usd_high !== r.gap_usd) usd += `–${r.gap_usd_high}`;
  cell.append(el('div', 'gapUsd', usd));

  if (r.gap_pct != null) {
    let pct = `${r.gap_pct}%`;
    if (r.gap_pct_high != null && r.gap_pct_high !== r.gap_pct) pct = `${r.gap_pct}–${r.gap_pct_high}%`;
    cell.append(el('div', 'gapPct', pct + ' off'));
  }
  cell.append(el('div', 'gapLabel', r.gap_basis === 'estimate' ? 'estimate' : 'verified'));
  return cell;
}

function renderRow(r) {
  const row = el('article', 'row');
  row.dataset.slug = r.slug;
  if (r.rank != null) row.classList.add('ranked');
  const urgent = isUrgent(r);
  if (urgent) row.classList.add('urgent');

  const head = el('button', 'rowHead');
  head.type = 'button';
  head.setAttribute('aria-expanded', EXPANDED.has(r.slug) ? 'true' : 'false');

  const main = el('div', 'rowMain');

  const nameLine = el('div', 'nameLine');
  if (r.rank != null) nameLine.append(el('span', 'rankBadge', `#${r.rank}`));
  nameLine.append(el('span', 'rname', r.name));
  main.append(nameLine);

  const meta = el('div', 'metaLine');
  const bits = [];
  if (r.neighborhood) bits.push(r.neighborhood);
  else if (r.borough) bits.push(r.borough);
  if (r.borough && r.neighborhood && r.borough !== r.neighborhood) bits.push(r.borough);
  if (r.cuisines && r.cuisines.length) bits.push(r.cuisines.slice(0, 2).join(', '));
  bits.forEach((b, i) => {
    if (i) meta.append(el('span', 'sep', '·'));
    meta.append(el('span', null, b));
  });
  if (r.price_tiers && r.price_tiers.length) {
    if (bits.length) meta.append(el('span', 'sep', '·'));
    meta.append(el('span', 'mono', r.price_tiers.join(' / ')));
  }
  if (r.meal_periods && r.meal_periods.length) {
    meta.append(el('span', 'sep', '·'));
    meta.append(el('span', null, r.meal_periods.join(', ')));
  }
  main.append(meta);

  const pills = el('div', 'pills');
  if (urgent) {
    pills.append(pill('crit', `book by ${fmtDate(DATA.book_by)}`,
      `RW window ends ${fmtDate(r.end_date)}`));
  } else if (r.end_date) {
    pills.append(pill('quiet', `thru ${fmtDate(r.end_date)}`,
      r.end_date_source === 'printed' ? 'End date printed by the restaurant'
        : 'End date from the listing API — not checked against the restaurant'));
  } else {
    pills.append(pill('warn', 'no end date', 'Neither the restaurant nor the listing gives an end date'));
  }
  // Only flag the date where the restaurant was ACTUALLY checked and either
  // printed nothing or contradicted itself. Plain 'api' is the unremarkable
  // default for ~625 rows — badging it would be noise, not information.
  if (r.end_date_source === 'conflict') {
    pills.append(pill('warn', 'date conflict',
      'Two verified sources disagree — the earlier date is shown'));
  } else if (r.end_date_source === 'api_fallback') {
    pills.append(pill('warn', 'date unprinted',
      'Checked: the restaurant prints no end date. The listing API value is shown.'));
  }
  if (r.sunday === true) {
    // Verified Sunday is a real find; an unverified one is just the listing.
    pills.append(r.sunday_source === 'verified'
      ? pill('value', 'sunday ✓', 'Sunday service verified against the restaurant')
      : pill('quiet', 'sunday', 'Sunday per the listing API — unverified'));
  }
  (r.recognition || []).forEach((x) => {
    const lvl = x.level ? ` ${x.level}` : '';
    pills.append(pill('rec', `${RECOG_LABEL[x.source] || x.source}${lvl}`,
      `${x.matched_name || r.name}${x.year ? ` · ${x.year}` : ''}${x.name_match_only ? ' · name match only' : ''}`));
  });
  (r.tags || []).forEach((t, i, arr) => {
    if (arr.findIndex((z) => z.tag === t.tag) !== i) return; // dedupe by tag
    pills.append(pill('tag', t.tag, t.confidence === 'low' ? 'Low-confidence match' : 'Dish tag match'));
  });
  if (pills.childNodes.length) main.append(pills);

  head.append(main, renderGapCell(r));
  head.addEventListener('click', () => toggleDetail(r, row, head));
  row.append(head);

  if (EXPANDED.has(r.slug)) row.append(renderDetail(r));
  return row;
}

function toggleDetail(r, row, head) {
  const open = EXPANDED.has(r.slug);
  if (open) {
    EXPANDED.delete(r.slug);
    const d = row.querySelector('.detail');
    if (d) d.remove();
    head.setAttribute('aria-expanded', 'false');
  } else {
    EXPANDED.add(r.slug);
    row.append(renderDetail(r));
    head.setAttribute('aria-expanded', 'true');
  }
}

function field(dl, label, value, mono) {
  if (value == null || value === '') return;
  const wrap = el('div', 'dfield');
  wrap.append(el('dt', null, label));
  const dd = el('dd', mono ? 'mono' : null);
  if (value instanceof Node) dd.append(value); else dd.textContent = value;
  wrap.append(dd);
  dl.append(wrap);
}

const FLAG_TEXT = {
  book_by_aug16: 'Ends Aug 16 or sooner — book first',
  end_date_unprinted: 'Restaurant prints no end date',
  end_date_conflict: 'Verified sources disagree on the end date',
  saturday_service: 'Prints SATURDAY service — an exception to the program-wide Saturday exclusion',
  sunday_api_contradicted: 'Sunday status contradicts the listing API',
  no_sunday_dinner: 'No Sunday dinner service',
  price_api_wrong: 'Listing API price is wrong — corrected here',
  confirm_at_booking: 'Confirm the details when booking',
  dropped_in_verification: 'Dropped during verification — unverifiable claim',
  ambiguous_entity: 'Two participants share this name — caveat cannot be attributed',
  two_course_tier: 'This tier buys TWO courses, not three',
};

function renderDetail(r) {
  const d = el('section', 'detail');

  if (r.verdict_note) {
    const n = el('div', 'note');
    n.append(el('strong', null, r.grade ? `Verdict [${r.grade}] ` : 'Verdict '));
    n.append(document.createTextNode(r.verdict_note));
    d.append(n);
  }
  if (r.notes) {
    const n = el('div', 'note warn');
    n.append(el('strong', null, 'Caveat '));
    n.append(document.createTextNode(r.notes));
    d.append(n);
  }
  (r.flags || []).forEach((f) => {
    if (!FLAG_TEXT[f]) return;
    if (f === 'book_by_aug16') return; // already a pill on the row
    const n = el('div', 'note warn');
    n.append(document.createTextNode(FLAG_TEXT[f]));
    d.append(n);
  });

  const dl = el('dl', 'dgrid');
  field(dl, 'RW price', r.rw_price != null
    ? money(r.rw_price) + (r.price_source === 'verified' ? ' (verified)' : '')
    : (r.price_tiers || []).join(' / ') || null, true);
  field(dl, 'Comparable', r.comparable_usd != null
    ? money(r.comparable_usd) + (r.comparable_usd_high ? `–${r.comparable_usd_high}` : '')
    : null, true);
  field(dl, 'Gap basis', r.gap_basis === 'verified' ? 'Verified — own-menu arithmetic'
    : r.gap_basis === 'estimate' ? 'Heuristic estimate — triage only' : 'No comparable published');
  field(dl, 'Window ends', r.end_date
    ? `${fmtDate(r.end_date)}${r.end_date_source === 'printed' ? ' (printed)' : r.end_date_source === 'conflict' ? ' (conservative)' : ' (listing API)'}`
    : 'Not stated', true);
  field(dl, 'Days / service', r.days);
  field(dl, 'Sunday', r.sunday === true ? (r.sunday_source === 'verified' ? 'Yes — verified' : 'Yes — per listing (unverified)')
    : r.sunday === false ? 'No — verified'
    : 'Not established');
  field(dl, 'Courses', r.courses != null ? `${r.courses}-course` : null);
  field(dl, 'Meal periods', (r.meal_periods || []).join(', ') || null);
  field(dl, 'Price tiers', (r.price_tiers || []).join(' / ') || null, true);
  field(dl, 'Cuisines', (r.cuisines || []).join(', ') || null);
  field(dl, 'Address', r.address || (r.borough ? `${r.borough} — address unavailable` : null));
  field(dl, 'Final-list rank', r.rank != null ? `#${r.rank} of 15` : null, true);
  field(dl, 'Menu', r.menu_state === 'pdf' ? 'Official PDF published'
    : r.menu_state === 'image_only' ? 'PDF is image-only — not machine-readable'
    : 'No RW menu published');
  d.append(dl);

  if ((r.recognition || []).length) {
    const s = el('div', 'dsec');
    s.append(el('h4', null, 'Recognition'));
    const ul = el('ul', 'snips');
    r.recognition.forEach((x) => {
      const li = el('li', 'snip');
      const label = `${RECOG_LABEL[x.source] || x.source}${x.level ? ` — ${x.level}` : ''}${x.year ? ` (${x.year})` : ''}`;
      if (x.url) {
        const a = el('a', null, label);
        a.href = x.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
        li.append(a);
      } else li.append(el('span', null, label));
      if (x.name_match_only) li.append(pill('quiet', 'name match only',
        'Matched on name alone — this source publishes no addresses'));
      if (x.matched_name && fold(x.matched_name) !== fold(r.name)) {
        li.append(el('span', 'gapLabel', `matched “${x.matched_name}”`));
      }
      ul.append(li);
    });
    s.append(ul);
    d.append(s);
  }

  if ((r.tags || []).length) {
    const s = el('div', 'dsec');
    s.append(el('h4', null, 'Dish tags'));
    const ul = el('ul', 'snips');
    r.tags.forEach((t) => {
      const li = el('li', 'snip');
      li.append(pill('tag', t.tag));
      if (t.confidence === 'low') li.append(pill('warn', 'low confidence'));
      if (t.snippet) {
        const q = el('q');
        // Highlight the matched keyword inside the snippet, without innerHTML.
        const kw = t.keyword;
        const idx = kw ? fold(t.snippet).indexOf(fold(kw)) : -1;
        if (idx >= 0) {
          q.append(document.createTextNode(t.snippet.slice(0, idx)));
          q.append(el('mark', null, t.snippet.slice(idx, idx + kw.length)));
          q.append(document.createTextNode(t.snippet.slice(idx + kw.length)));
        } else {
          q.textContent = t.snippet;
        }
        li.append(q);
      }
      ul.append(li);
    });
    s.append(ul);
    d.append(s);
  }

  const links = el('div', 'linkRow');
  const addLink = (href, text, primary) => {
    if (!href) return;
    const a = el('a', `linkBtn${primary ? ' primary' : ''}`, text);
    a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
    links.append(a);
  };
  addLink(r.links && r.links.reservation, 'Book a table', true);
  addLink(r.links && r.links.menu, r.menu_state === 'image_only' ? 'Menu PDF (image-only)' : 'Menu PDF');
  addLink(r.links && r.links.listing, 'Official listing');
  addLink(r.links && r.links.website, 'Restaurant site');
  if (links.childNodes.length) d.append(links);

  return d;
}

/* ---------- facet panel ------------------------------------------------- */

function buildFacets() {
  const host = $('#facets');
  host.textContent = '';

  // Counts reflect the CURRENT result set so the panel stays honest as you filter.
  const visible = ROWS.filter(matches);

  for (const f of FACETS) {
    const counts = new Map();
    for (const r of visible) for (const v of new Set(f.values(r))) counts.set(v, (counts.get(v) || 0) + 1);
    // Selected values must stay visible even at zero, or they can't be turned off.
    for (const v of FILTERS[f.key]) if (!counts.has(v)) counts.set(v, 0);

    let entries = [...counts.entries()];
    if (!entries.length) continue;

    if (f.key === 'tier') entries.sort((a, b) => (parseInt(a[0].replace(/\D/g, ''), 10) || 0) - (parseInt(b[0].replace(/\D/g, ''), 10) || 0));
    else if (f.key === 'tag' || f.key === 'neighborhood' || f.key === 'cuisine') entries.sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
    else entries.sort((a, b) => String(labelFor(f, a[0])).localeCompare(String(labelFor(f, b[0]))));

    const sec = el('div', 'facet');
    sec.append(el('h3', null, f.title));
    const box = el('div', f.scroll ? 'scroller' : null);
    const chips = el('div', 'chips');
    for (const [v, c] of entries) {
      const b = el('button', 'chip');
      b.type = 'button';
      b.setAttribute('aria-pressed', FILTERS[f.key].has(v) ? 'true' : 'false');
      b.append(document.createTextNode(labelFor(f, v)));
      b.append(el('span', 'c', String(c)));
      b.addEventListener('click', () => {
        FILTERS[f.key].has(v) ? FILTERS[f.key].delete(v) : FILTERS[f.key].add(v);
        apply();
      });
      chips.append(b);
    }
    box.append(chips);
    sec.append(box);
    host.append(sec);
  }

  // Date facet is its own control, not a chipset.
  const sec = el('div', 'facet');
  sec.append(el('h3', null, 'Still bookable on'));
  const wrapEl = el('div', 'dateFacet');
  const input = el('input');
  input.type = 'date';
  input.id = 'bookableBy';
  if (FILTERS.bookableBy) input.value = FILTERS.bookableBy;
  input.addEventListener('change', () => { FILTERS.bookableBy = input.value || null; apply(); });
  wrapEl.append(input);
  const clear = el('button', 'chip', 'Any date');
  clear.type = 'button';
  clear.setAttribute('aria-pressed', FILTERS.bookableBy ? 'false' : 'true');
  clear.addEventListener('click', () => { FILTERS.bookableBy = null; apply(); });
  wrapEl.append(clear);
  sec.append(wrapEl);
  host.append(sec);
}

/* ---------- apply / render loop ----------------------------------------- */

function activeCount() {
  let n = FACETS.reduce((acc, f) => acc + FILTERS[f.key].size, 0);
  if (FILTERS.bookableBy) n += 1;
  if (QUERY) n += 1;
  return n;
}

function apply() {
  const out = ROWS.filter(matches).sort(SORTS[SORT] || SORTS.gap_usd_desc);

  const host = $('#rows');
  host.textContent = '';
  const frag = document.createDocumentFragment();
  out.forEach((r) => frag.append(renderRow(r)));
  host.append(frag);

  $('#shown').textContent = out.length;
  $('#total').textContent = ROWS.length;
  const urgent = out.filter(isUrgent).length;
  $('#urgentCount').textContent = urgent ? `· ${urgent} ending by ${fmtDate(DATA.book_by)}` : '';
  $('#empty').hidden = out.length !== 0;

  const n = activeCount();
  const badge = $('#filterCount');
  badge.textContent = String(n);
  badge.hidden = n === 0;
  $('#clearBtn').hidden = n === 0;

  buildFacets();
  BANNER();
  writeHash();
}

function clearAll() {
  FACETS.forEach((f) => FILTERS[f.key].clear());
  FILTERS.bookableBy = null;
  QUERY = '';
  $('#q').value = '';
  apply();
}

/* ---------- URL state (so a filtered view survives a reload / bookmark) -- */

function writeHash() {
  const p = new URLSearchParams();
  FACETS.forEach((f) => { if (FILTERS[f.key].size) p.set(f.key, [...FILTERS[f.key]].join('~')); });
  if (FILTERS.bookableBy) p.set('by', FILTERS.bookableBy);
  if (QUERY) p.set('q', QUERY);
  if (SORT !== 'gap_usd_desc') p.set('sort', SORT);
  const s = p.toString();
  history.replaceState(null, '', s ? `#${s}` : location.pathname + location.search);
}

function readHash() {
  const raw = location.hash.replace(/^#/, '');
  if (!raw) return;
  const p = new URLSearchParams(raw);
  FACETS.forEach((f) => {
    const v = p.get(f.key);
    if (v) v.split('~').filter(Boolean).forEach((x) => FILTERS[f.key].add(x));
  });
  if (p.get('by')) FILTERS.bookableBy = p.get('by');
  if (p.get('q')) { QUERY = fold(p.get('q')); $('#q').value = p.get('q'); }
  if (p.get('sort') && SORTS[p.get('sort')]) { SORT = p.get('sort'); $('#sort').value = SORT; }
}

/* ---------- theme ------------------------------------------------------- */

function initTheme() {
  const saved = localStorage.getItem('rw-theme');
  if (saved) document.documentElement.dataset.theme = saved;
  $('#themeToggle').addEventListener('click', () => {
    const cur = document.documentElement.dataset.theme
      || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('rw-theme', next);
  });
}

/* ---------- boot -------------------------------------------------------- */

function prepare(r) {
  // One folded haystack per row, built once — search is then a substring test.
  r._name = r.name || '';
  r._hay = fold([
    r.name, r.slug, r.borough, r.neighborhood, r.address,
    (r.cuisines || []).join(' '),
    (r.price_tiers || []).join(' '),
    (r.meal_periods || []).join(' '),
    (r.tags || []).map((t) => `${t.tag} ${t.keyword || ''}`).join(' '),
    (r.recognition || []).map((x) => `${RECOG_LABEL[x.source] || x.source} ${x.level || ''} ${x.matched_name || ''}`).join(' '),
    r.verdict, r.verdict_note, r.days,
  ].filter(Boolean).join(' • '));
  return r;
}

async function boot() {
  initTheme();

  let res;
  try {
    res = await fetch(DATA_URL, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    DATA = await res.json();
  } catch (err) {
    $('#rows').append(Object.assign(el('div', 'empty'), { textContent:
      `Could not load ${DATA_URL} — ${err.message}. Run: python src/export_site_data.py` }));
    return;
  }

  ROWS = (DATA.restaurants || []).map(prepare);

  $('#seasonLabel').textContent = DATA.season_label || '';
  $('#footProvenance').textContent =
    `${ROWS.length} participants · listing snapshot ${DATA.snapshot_date || '—'} · ` +
    `verified facts hand-checked ${DATA.verified_asof || '—'} · built ${(DATA.generated_at || '').slice(0, 10)}`;

  // Default view: everything still bookable today, biggest gap first.
  FILTERS.bookableBy = todayISO();
  readHash();

  $('#q').addEventListener('input', (e) => { QUERY = fold(e.target.value.trim()); apply(); });
  $('#sort').addEventListener('change', (e) => { SORT = e.target.value; apply(); });
  $('#clearBtn').addEventListener('click', clearAll);
  $('#clearBtn2').addEventListener('click', clearAll);

  // The estimate caveat only matters while estimates are actually in view.
  const banner = $('#estBanner');
  const bannerDismissed = localStorage.getItem('rw-banner') === 'off';
  $('#dismissBanner').addEventListener('click', () => {
    localStorage.setItem('rw-banner', 'off');
    banner.hidden = true;
  });
  $('#verifiedOnly').addEventListener('click', () => {
    FILTERS.basis.clear();
    FILTERS.basis.add('verified');
    apply();
  });
  BANNER = () => {
    banner.hidden = bannerDismissed
      || SORT !== 'gap_usd_desc'
      || FILTERS.basis.size > 0;
  };

  const fb = $('#filterBtn');
  fb.addEventListener('click', () => {
    const open = fb.getAttribute('aria-expanded') === 'true';
    fb.setAttribute('aria-expanded', open ? 'false' : 'true');
    $('#panel').hidden = open;
  });

  apply();
}

boot();
