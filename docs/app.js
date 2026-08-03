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
    .replace(/[̀-ͯ]/g, '')          // combining diacritics
    .replace(/[‘’ʼ′]/g, "'")  // curly apostrophes -> '
    .replace(/[–—‒−]/g, '-')  // en/em dash, minus -> -
    .replace(/[“”]/g, '"')
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
  bookableBy: null,       // ISO date — keep rows still open ON this date
  endingBy: null,         // ISO date — keep rows that CLOSE on/before this date
  savedOnly: false,       // your own shortlist
};

const PAGE_SIZE = 50;     // 648 rows is ~100 phone-screens; render in chunks
let RENDERED = 0;
let RESULTS = [];
let LAST_GROUP = null;
let BOOTED = false;       // suppress scroll correction during the first render

/** Document offset of the top of the results list. */
const RESULTS_TOP = () => {
  const m = document.querySelector('.results');
  return m ? m.getBoundingClientRect().top + scrollY : 0;
};

/* Your own shortlist, kept in this browser. The dataset ships a curated
   ranking, but choosing what to actually book is a separate, personal pass. */
const SAVED = new Set(JSON.parse(localStorage.getItem('rw-saved') || '[]'));
const persistSaved = () =>
  localStorage.setItem('rw-saved', JSON.stringify([...SAVED]));
let SORT = 'gap_usd_desc';
let QUERY = '';
let BANNER = () => {};   // set during boot, once the banner nodes exist
let FACET_FIND = '';                    // "find a filter" query
const EXPANDED_FACETS = new Set();      // facets the user expanded past the cap

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
  // Seeded from the exported tag_vocabulary (= config/dish_tags.json keys), so a
  // newly configured tag that matched nothing shows "0" instead of vanishing —
  // a missing chip reads as a broken pipeline rather than as the real answer.
  { key: 'tag',         title: 'Dish tags',     values: (r) => [...new Set((r.tags || []).map((t) => t.tag))],
                                                seed: () => (DATA && DATA.tag_vocabulary) || [] },
  { key: 'menu',        title: 'Menu PDF',      values: (r) => [r.menu_state || 'none'],
                                                labels: { pdf: 'Readable PDF', image_only: 'Image-only PDF', none: 'No menu published' } },
];

const RECOG_LABEL = { michelin: 'Michelin', james_beard: 'James Beard', nyt: 'NYT' };
const labelFor = (facet, v) => (facet.labels && facet.labels[v]) || RECOG_LABEL[v] || v;

/* ---------- filtering --------------------------------------------------- */

/** `exceptFacet` skips one facet's own selection — used when counting that
 *  facet's chips, so its unselected values keep a live, non-zero count. */
function matches(r, exceptFacet) {
  if (QUERY) {
    if (!r._hay.includes(QUERY)) return false;
  }

  for (const f of FACETS) {
    if (f.key === exceptFacet) continue;
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
  if (FILTERS.endingBy) {
    // "closing soon" — here an unknown end date is excluded, because we can't
    // claim a restaurant is expiring when nothing says so.
    if (!r.end_date || r.end_date > FILTERS.endingBy) return false;
  }
  if (FILTERS.savedOnly && !SAVED.has(r.slug)) return false;

  return true;
}

/** Match quality against the query, lower = better.
 *  The haystack deliberately spans borough/neighborhood/cuisine so you can
 *  search those too — but that means "Manhatta" (a restaurant) also matches
 *  all 549 Manhattan rows. Rank name matches above field matches so the
 *  restaurant you typed comes first; the broader matches still follow. */
function relevance(r) {
  if (!QUERY) return 0;
  const n = r._nf;
  if (n === QUERY) return 0;
  if (n.startsWith(QUERY)) return 1;
  if (r._words.some((w) => w.startsWith(QUERY))) return 2;
  if (n.includes(QUERY)) return 3;
  return 4;
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

  // A negative gap means RW costs MORE than the comparable. 38 estimates are
  // negative; rendering "$-11 / -22% off" would read as a discount.
  const over = r.gap_usd < 0;
  if (over) cell.classList.add('over');

  let usd = money(Math.abs(r.gap_usd));
  if (r.gap_usd_high != null && r.gap_usd_high !== r.gap_usd) usd += `–${Math.abs(r.gap_usd_high)}`;
  cell.append(el('div', 'gapUsd', (over ? '+' : '') + usd));

  if (r.gap_pct != null) {
    let pct = `${Math.abs(r.gap_pct)}%`;
    if (r.gap_pct_high != null && r.gap_pct_high !== r.gap_pct) {
      pct = `${Math.abs(r.gap_pct)}–${Math.abs(r.gap_pct_high)}%`;
    }
    cell.append(el('div', 'gapPct', pct + (over ? ' MORE' : ' off')));
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
  if (SAVED.has(r.slug)) pills.append(pill('saved', '★ saved'));
  if (urgent) {
    // Show the row's OWN closing date, not the program-wide Aug 16 headline —
    // 18 rows close earlier than that, and a tooltip is not a disclosure.
    pills.append(pill('crit', `book by ${fmtDate(r.end_date)}`,
      r.end_date_source === 'conflict'
        ? 'Two verified sources disagree — the earlier date is shown'
        : `RW window ends ${fmtDate(r.end_date)}`));
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
  // Dedupe on (source, level) for the row — Union Square Cafe alone has 12
  // badges that would otherwise render as a wall of identical pills. The full
  // per-year list stays in the detail panel.
  const seenRec = new Set();
  (r.recognition || []).forEach((x) => {
    const key = `${x.source}|${x.level}`;
    if (seenRec.has(key)) return;
    seenRec.add(key);
    const n = (r.recognition || []).filter((y) => `${y.source}|${y.level}` === key).length;
    const lvl = x.level ? ` ${x.level}` : '';
    pills.append(pill('rec', `${RECOG_LABEL[x.source] || x.source}${lvl}${n > 1 ? ` ×${n}` : ''}`,
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
  // Name the tier the estimate was computed against — the gap is always taken
  // at ONE tier, and without saying which, the comparable minus the headline
  // price appears not to add up.
  field(dl, 'Estimated at tier', r.estimate_tier || null, true);
  field(dl, 'Gap basis', r.gap_basis === 'verified' ? 'Verified — own-menu arithmetic'
    : r.gap_basis === 'estimate'
      ? `Heuristic estimate — triage only (${r.estimate_confidence || '?'} confidence)`
      : 'No comparable published');
  field(dl, 'Window ends', r.end_date
    ? `${fmtDate(r.end_date)}${r.end_date_source === 'printed' ? ' (printed)' : r.end_date_source === 'conflict' ? ' (conservative)' : ' (listing API)'}`
    : 'Not stated', true);
  field(dl, 'Days / service', r.days);
  // "verified" only when it actually came from the restaurant. 160 rows are
  // false purely because the listing API says so — that is not verification.
  field(dl, 'Sunday',
    r.sunday === true
      ? (r.sunday_source === 'verified' ? 'Yes — verified' : 'Yes — per listing (unverified)')
      : r.sunday === false
        ? (r.sunday_source === 'verified' ? 'No — verified' : 'No — per listing (unverified)')
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
      if (x.rank != null) li.append(pill('rec', `No. ${x.rank}`, 'Rank on the NYT 100 list'));
      if (x.name_match_only) li.append(pill('quiet', 'name match only',
        'Matched on name alone — this source publishes no addresses'));
      if (x.matched_name && fold(x.matched_name) !== fold(r.name)) {
        li.append(el('span', 'gapLabel', `matched “${x.matched_name}”`));
      }
      // The award name (and the chef) exist only in the raw James Beard file;
      // without this the (source, level, year) dedupe would hide them.
      if (x.awards && x.awards.length) {
        li.append(el('span', 'awardList', x.awards.join(' · ')));
      }
      if (x.via) li.append(el('span', 'gapLabel', `via ${x.via}`));
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

  // Save lives here rather than on the row: choosing what to book means
  // reading the verdict and the caveats first, so you are already expanded.
  const save = el('button', 'linkBtn saveBtn');
  save.type = 'button';
  const paint = () => {
    const on = SAVED.has(r.slug);
    save.textContent = on ? '★ Saved' : '☆ Save';
    save.classList.toggle('on', on);
    save.setAttribute('aria-pressed', on ? 'true' : 'false');
  };
  paint();
  save.addEventListener('click', () => {
    SAVED.has(r.slug) ? SAVED.delete(r.slug) : SAVED.add(r.slug);
    persistSaved();
    paint();
    // Repaint the row's pills and the Saved preset count without collapsing
    // the panel the user is reading.
    buildPresets();
    const pills = document.querySelector(`.row[data-slug="${CSS.escape(r.slug)}"] .pills`);
    if (pills) {
      const star = pills.querySelector('.pill.saved');
      if (SAVED.has(r.slug) && !star) pills.prepend(pill('saved', '★ saved'));
      if (!SAVED.has(r.slug) && star) star.remove();
    }
  });
  links.append(save);
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
  // Every chip node is destroyed and rebuilt on each render, which drops
  // keyboard focus to <body> and swallows the next Tab. Remember which chip
  // was focused and restore it after the rebuild.
  const active = document.activeElement;
  const refocus = active && active.classList && active.classList.contains('chip')
    ? { facet: active.dataset.facet, value: active.dataset.value } : null;

  host.textContent = '';

  for (const f of FACETS) {
    // Count each facet against the rows surviving every OTHER facet — never
    // its own. Counting against the fully-filtered set would zero out every
    // unselected value in the facet you just used, making a second selection
    // in the same facet impossible (i.e. OR-within-a-facet unreachable).
    const visible = ROWS.filter((r) => matches(r, f.key));

    const counts = new Map();
    if (f.seed) for (const v of f.seed()) counts.set(v, 0);
    for (const r of visible) for (const v of new Set(f.values(r))) counts.set(v, (counts.get(v) || 0) + 1);
    // Selected values must stay visible even at zero, or they can't be turned off.
    for (const v of FILTERS[f.key]) if (!counts.has(v)) counts.set(v, 0);

    let entries = [...counts.entries()];
    // "Find a filter" searches across every group at once — with 76
    // neighborhoods and 56 cuisines, scanning by eye is the slow part.
    if (FACET_FIND) {
      entries = entries.filter(([v]) =>
        fold(labelFor(f, v)).includes(FACET_FIND) || fold(f.title).includes(FACET_FIND));
    }
    if (!entries.length) continue;

    if (f.key === 'tier') entries.sort((a, b) => (parseInt(a[0].replace(/\D/g, ''), 10) || 0) - (parseInt(b[0].replace(/\D/g, ''), 10) || 0));
    else if (f.key === 'tag' || f.key === 'neighborhood' || f.key === 'cuisine') entries.sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
    else entries.sort((a, b) => String(labelFor(f, a[0])).localeCompare(String(labelFor(f, b[0]))));

    const sec = el('div', 'facet');
    sec.append(el('h3', null, f.title));
    const box = el('div', f.scroll ? 'scroller' : null);
    const chips = el('div', 'chips');

    // Long tails (76 neighborhoods, 56 cuisines) are collapsed to the most
    // populated values; selected ones always stay visible so they can be
    // turned off. Searching or expanding reveals the rest.
    const CAP = 12;
    const capped = !FACET_FIND && !EXPANDED_FACETS.has(f.key) && entries.length > CAP + 3;
    const hiddenCount = capped ? entries.length - CAP : 0;
    if (capped) {
      const keep = entries.slice(0, CAP);
      const sel = entries.slice(CAP).filter(([v]) => FILTERS[f.key].has(v));
      entries = keep.concat(sel);
    }

    for (const [v, c] of entries) {
      const b = el('button', 'chip');
      b.type = 'button';
      b.dataset.facet = f.key;
      b.dataset.value = String(v);
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
    if (hiddenCount > 0) {
      const more = el('button', 'moreLink', `Show all ${entries.length + hiddenCount - (entries.length - CAP)} …`);
      more.textContent = `Show ${hiddenCount} more`;
      more.type = 'button';
      more.addEventListener('click', () => { EXPANDED_FACETS.add(f.key); buildFacets(); });
      sec.append(more);
    }
    host.append(sec);
  }

  // Date facet is its own control, not a chipset — but it must still answer to
  // "find a filter", or it's the one group that never disappears when you search.
  if (FACET_FIND && !'still bookable on date'.includes(FACET_FIND)) {
    if (refocus) {
      const again = host.querySelector(
        `.chip[data-facet="${CSS.escape(refocus.facet)}"][data-value="${CSS.escape(refocus.value)}"]`);
      if (again) again.focus();
    }
    return;
  }
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

  if (refocus) {
    const again = host.querySelector(
      `.chip[data-facet="${CSS.escape(refocus.facet)}"][data-value="${CSS.escape(refocus.value)}"]`);
    if (again) again.focus();
  }
}

/* ---------- apply / render loop ----------------------------------------- */

/* ---------- quick views -------------------------------------------------- */

/* Each preset is a named destination: what it selects, and how to tell whether
   you're already there. They exist so the common questions ("what closes this
   week?", "show me the shortlist") don't require opening a 164-chip panel. */
const PRESETS = [
  {
    // Only appears once you've actually saved something.
    key: 'saved', label: '★ Saved',
    show: () => SAVED.size > 0,
    count: () => SAVED.size,
    is: () => FILTERS.savedOnly,
    set() { FILTERS.savedOnly = true; },
  },
  {
    key: 'ranked', label: 'The 15',
    count: () => ROWS.filter((r) => r.rank != null).length,
    is: () => FILTERS.verdict.has('Ranked pick'),
    set() { FILTERS.verdict.add('Ranked pick'); SORT = 'rank_asc'; },
  },
  {
    key: 'urgent', label: 'Closing soon',
    count: () => ROWS.filter((r) => r.end_date && r.end_date <= DATA.book_by).length,
    is: () => FILTERS.endingBy === DATA.book_by,
    set() { FILTERS.endingBy = DATA.book_by; SORT = 'end_asc'; },
  },
  {
    key: 'verified', label: 'Verified gaps',
    count: () => ROWS.filter((r) => r.gap_basis === 'verified').length,
    is: () => FILTERS.basis.has('verified'),
    set() { FILTERS.basis.add('verified'); SORT = 'gap_usd_desc'; },
  },
  {
    key: 'sunday', label: 'Sunday',
    count: () => ROWS.filter((r) => r.sunday === true).length,
    is: () => FILTERS.sunday.has('yes'),
    set() { FILTERS.sunday.add('yes'); },
  },
  {
    key: 'michelin', label: 'Michelin',
    count: () => ROWS.filter((r) => (r.recognition || []).some((x) => x.source === 'michelin')).length,
    is: () => FILTERS.recognition.has('michelin'),
    set() { FILTERS.recognition.add('michelin'); },
  },
];

function buildPresets() {
  const host = $('#presets');
  host.textContent = '';
  PRESETS.filter((p) => !p.show || p.show()).forEach((p) => {
    const b = el('button', 'preset');
    b.type = 'button';
    b.setAttribute('aria-pressed', p.is() ? 'true' : 'false');
    b.append(document.createTextNode(p.label));
    b.append(el('span', 'c', String(p.count())));
    b.addEventListener('click', () => {
      const wasOn = p.is();
      clearAll(true);          // a quick view is a destination, not another layer
      if (!wasOn) p.set();     // clicking the active one takes you back to all
      apply();
    });
    host.append(b);
  });
}

/* ---------- active filters (visible once the panel is closed) ------------ */

function buildActiveFilters() {
  const host = $('#activeFilters');
  host.textContent = '';
  const add = (label, value, onRemove) => {
    const c = el('button', 'afChip');
    c.type = 'button';
    c.title = `Remove ${label}: ${value}`;
    c.append(el('span', 'k', label));
    c.append(document.createTextNode(value));
    c.append(el('span', 'x', '×'));
    c.addEventListener('click', () => { onRemove(); apply(); });
    host.append(c);
  };

  FACETS.forEach((f) => {
    FILTERS[f.key].forEach((v) => {
      add(f.title, labelFor(f, v), () => FILTERS[f.key].delete(v));
    });
  });
  if (FILTERS.bookableBy) {
    add('Open on', fmtDate(FILTERS.bookableBy), () => { FILTERS.bookableBy = null; });
  }
  if (FILTERS.endingBy) {
    add('Closes by', fmtDate(FILTERS.endingBy), () => { FILTERS.endingBy = null; });
  }
  if (FILTERS.savedOnly) {
    add('Shortlist', `${SAVED.size} saved`, () => { FILTERS.savedOnly = false; });
  }
  if (QUERY) {
    add('Search', $('#q').value, () => { QUERY = ''; $('#q').value = ''; });
  }
  host.hidden = host.childNodes.length === 0;
}

/* ---------- apply / render loop ----------------------------------------- */

function activeCount() {
  let n = FACETS.reduce((acc, f) => acc + FILTERS[f.key].size, 0);
  if (FILTERS.bookableBy) n += 1;
  if (FILTERS.endingBy) n += 1;
  if (FILTERS.savedOnly) n += 1;
  if (QUERY) n += 1;
  return n;
}

/** Rows are grouped by closing date when sorted by date — the only sort where
 *  a run of adjacent rows shares a meaningful heading. */
const groupKeyOf = (r) =>
  (SORT === 'end_asc' || SORT === 'end_desc') ? (r.end_date || 'none') : null;

function groupHeader(key) {
  const h = el('div', 'groupHead');
  const n = RESULTS.filter((r) => groupKeyOf(r) === key).length;
  if (key === 'none') {
    h.append(el('h2', null, 'No end date published'));
  } else {
    const urgent = DATA.book_by && key <= DATA.book_by;
    if (urgent) h.classList.add('urgent');
    h.append(el('h2', null, `${urgent ? 'Closes' : 'Runs through'} ${fmtDate(key)}`));
  }
  h.append(el('span', 'n', `${n} restaurant${n === 1 ? '' : 's'}`));
  return h;
}

/** Append the next chunk — first render, Show more, and scroll all use this. */
function renderPage() {
  const frag = document.createDocumentFragment();
  const slice = RESULTS.slice(RENDERED, RENDERED + PAGE_SIZE);

  slice.forEach((r) => {
    const key = groupKeyOf(r);
    if (key !== null && key !== LAST_GROUP) {
      frag.append(groupHeader(key));
      LAST_GROUP = key;
    }
    frag.append(renderRow(r));
  });

  $('#rows').append(frag);
  RENDERED += slice.length;

  const left = RESULTS.length - RENDERED;
  const more = $('#showMore');
  more.hidden = left <= 0;
  more.textContent = left > 0 ? `Show ${Math.min(PAGE_SIZE, left)} more  ·  ${left} remaining` : '';
}

function apply() {
  const cmp = SORTS[SORT] || SORTS.gap_usd_desc;
  // With a query active, match quality leads and the chosen sort orders within
  // each tier, so the sort control still does what it says.
  RESULTS = ROWS.filter(matches)
    .sort(QUERY ? (a, b) => relevance(a) - relevance(b) || cmp(a, b) : cmp);

  $('#rows').textContent = '';
  RENDERED = 0;
  LAST_GROUP = null;
  renderPage();

  // Filtering from deep in the list otherwise strands you mid-page — often
  // past the end of a now-shorter result set. Only correct the scroll when
  // you're actually below the results, and never on a Show-more append.
  if (BOOTED && scrollY > RESULTS_TOP()) {
    window.scrollTo({ top: Math.max(0, RESULTS_TOP() - 8), behavior: 'instant' });
  }

  // Presets and URL state both change SORT programmatically; keep the control
  // showing the truth rather than whatever was last picked by hand.
  if ($('#sort').value !== SORT) $('#sort').value = SORT;

  $('#shown').textContent = RESULTS.length;
  $('#total').textContent = ROWS.length;
  const urgent = RESULTS.filter(isUrgent).length;
  $('#urgentCount').textContent = urgent ? `· ${urgent} ending by ${fmtDate(DATA.book_by)}` : '';
  $('#empty').hidden = RESULTS.length !== 0;

  const n = activeCount();
  const badge = $('#filterCount');
  badge.textContent = String(n);
  badge.hidden = n === 0;
  $('#clearBtn').hidden = n === 0;

  if (VIEW === 'map') drawMarkers();

  buildPresets();
  buildActiveFilters();

  buildFacets();
  BANNER();
  writeHash();
}

/** `silent` resets state without re-rendering — presets clear then set, and
 *  would otherwise render an empty intermediate view. */
function clearAll(silent) {
  FACETS.forEach((f) => FILTERS[f.key].clear());
  FILTERS.bookableBy = null;
  FILTERS.endingBy = null;
  FILTERS.savedOnly = false;
  QUERY = '';
  $('#q').value = '';
  if (!silent) apply();
}

/* ---------- URL state (so a filtered view survives a reload / bookmark) -- */

function writeHash() {
  const p = new URLSearchParams();
  FACETS.forEach((f) => { if (FILTERS[f.key].size) p.set(f.key, [...FILTERS[f.key]].join('~')); });
  // "any" is written explicitly: an absent `by` means "no state yet", which
  // boot resolves to today — so clearing the date filter has to be recorded,
  // or a reload silently re-applies today's date.
  p.set('by', FILTERS.bookableBy || 'any');
  if (FILTERS.endingBy) p.set('to', FILTERS.endingBy);
  if (FILTERS.savedOnly) p.set('saved', '1');
  if (VIEW === 'map') p.set('view', 'map');
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
  const by = p.get('by');
  if (by) FILTERS.bookableBy = by === 'any' ? null : by;
  if (p.get('to')) FILTERS.endingBy = p.get('to');
  if (p.get('saved') === '1') FILTERS.savedOnly = true;
  if (p.get('q')) { QUERY = fold(p.get('q')); $('#q').value = p.get('q'); }
  // Object.hasOwn, not truthiness: "sort=constructor" inherits from
  // Object.prototype, would pass a truthy check and then be used as a comparator.
  const s = p.get('sort');
  if (s && Object.hasOwn(SORTS, s)) { SORT = s; $('#sort').value = SORT; }
}

/* ---------- map ---------------------------------------------------------- */

/* Leaflet and its tiles are the only third-party assets this page uses, and
   they are fetched ONLY when the map is first opened — the list view stays
   entirely self-contained. */
const LEAFLET_CSS = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
const LEAFLET_JS = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
const TILES = {
  light: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
  dark: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
};
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ' +
  '&copy; <a href="https://carto.com/attributions">CARTO</a>';

let MAP = null;
let MAP_LAYER = null;
let MAP_TILES = null;
let MAP_LOADING = null;
let VIEW = 'list';

const isDark = () =>
  (document.documentElement.dataset.theme
    || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')) === 'dark';

function loadLeaflet() {
  if (window.L) return Promise.resolve();
  if (MAP_LOADING) return MAP_LOADING;
  MAP_LOADING = new Promise((resolve, reject) => {
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = LEAFLET_CSS;
    document.head.append(css);
    const js = document.createElement('script');
    js.src = LEAFLET_JS;
    js.onload = resolve;
    js.onerror = () => reject(new Error('could not load the map library'));
    document.head.append(js);
  });
  return MAP_LOADING;
}

const markerColour = (r) =>
  isUrgent(r) ? getComputedStyle(document.documentElement).getPropertyValue('--crit')
    : r.gap_basis === 'verified' ? getComputedStyle(document.documentElement).getPropertyValue('--value')
    : r.gap_basis === 'estimate' ? getComputedStyle(document.documentElement).getPropertyValue('--warn')
    : getComputedStyle(document.documentElement).getPropertyValue('--ink-3');

function popupFor(r) {
  const box = el('div', 'mapPop');
  box.append(el('h3', null, (r.rank != null ? `#${r.rank}  ` : '') + r.name));
  const bits = [r.neighborhood, (r.cuisines || [])[0], (r.price_tiers || []).join('/')]
    .filter(Boolean).join(' · ');
  box.append(el('div', 'meta', bits));

  if (r.gap_usd != null) {
    const g = el('div', `gap${r.gap_basis === 'estimate' ? ' est' : ''}`);
    const over = r.gap_usd < 0;
    g.textContent = `${over ? '+' : ''}${money(Math.abs(r.gap_usd))}`
      + (r.gap_pct != null ? ` · ${Math.abs(r.gap_pct)}% ${over ? 'MORE' : 'off'}` : '')
      + (r.gap_basis === 'estimate' ? '  (est.)' : '');
    box.append(g);
  }
  box.append(el('div', 'meta',
    r.end_date ? `Runs through ${fmtDate(r.end_date)}` : 'No end date published'));

  const acts = el('div', 'acts');
  if (r.links && r.links.reservation) {
    const a = el('a', 'primary', 'Book');
    a.href = r.links.reservation; a.target = '_blank'; a.rel = 'noopener noreferrer';
    acts.append(a);
  }
  const det = el('button', null, 'Details');
  det.type = 'button';
  det.addEventListener('click', () => {
    // Jump back to the list with this one open.
    EXPANDED.add(r.slug);
    setView('list');
    const row = document.querySelector(`.row[data-slug="${CSS.escape(r.slug)}"]`);
    if (row) row.scrollIntoView({ block: 'center' });
    else { QUERY = fold(r.name); $('#q').value = r.name; apply(); }
  });
  acts.append(det);
  box.append(acts);
  return box;
}

function drawMarkers() {
  if (!MAP || !window.L) return;
  if (MAP_LAYER) MAP_LAYER.remove();
  MAP_LAYER = L.layerGroup().addTo(MAP);

  const pts = RESULTS.filter((r) => r.lat != null && r.lng != null);
  pts.forEach((r) => {
    const c = markerColour(r).trim();
    L.circleMarker([r.lat, r.lng], {
      radius: r.rank != null ? 8 : 6,
      color: c,
      weight: r.rank != null ? 3 : 1.5,
      fillColor: c,
      fillOpacity: SAVED.has(r.slug) ? 0.95 : 0.55,
    })
      .bindPopup(() => popupFor(r), { closeButton: true, maxWidth: 260 })
      .bindTooltip(r.name, { direction: 'top', offset: [0, -6] })
      .addTo(MAP_LAYER);
  });

  const missing = RESULTS.length - pts.length;
  $('#mapNote').textContent =
    `${pts.length} plotted${missing ? ` · ${missing} without usable coordinates` : ''}`;

  if (pts.length) {
    MAP.fitBounds(L.latLngBounds(pts.map((r) => [r.lat, r.lng])).pad(0.08),
      { animate: false, maxZoom: 15 });
  }
}

function paintTiles() {
  if (!MAP || !window.L) return;
  if (MAP_TILES) MAP_TILES.remove();
  MAP_TILES = L.tileLayer(TILES[isDark() ? 'dark' : 'light'], {
    attribution: TILE_ATTR, maxZoom: 19, detectRetina: true,
  }).addTo(MAP);
}

async function openMap() {
  const wrap = $('#mapWrap');
  try {
    await loadLeaflet();
  } catch (err) {
    $('#map').innerHTML = '';
    $('#map').append(Object.assign(el('div', 'mapFail'), {
      textContent: `The map needs to load Leaflet from unpkg.com and tiles from CARTO — ${err.message}. The list view works offline; everything here is also in it.`,
    }));
    return;
  }
  if (!MAP) {
    MAP = L.map('map', { scrollWheelZoom: false, zoomControl: true });
    MAP.setView([40.7549, -73.9840], 12);
    paintTiles();
  }
  MAP.invalidateSize();
  drawMarkers();
}

function setView(v) {
  VIEW = v;
  const onMap = v === 'map';
  $('#mapWrap').hidden = !onMap;
  $('#rows').hidden = onMap;
  $('#showMore').hidden = onMap || RENDERED >= RESULTS.length;
  const b = $('#viewBtn');
  b.textContent = onMap ? 'List' : 'Map';
  b.setAttribute('aria-pressed', onMap ? 'true' : 'false');
  if (onMap) openMap();
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
    if (MAP) { paintTiles(); drawMarkers(); }   // tiles + marker colours are themed
  });
}

/* ---------- boot -------------------------------------------------------- */

function prepare(r) {
  // One folded haystack per row, built once — search is then a substring test.
  r._name = r.name || '';
  r._nf = fold(r._name);
  r._words = r._nf.split(/[^a-z0-9']+/).filter(Boolean);
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
  $('#facetFind').addEventListener('input', (e) => {
    FACET_FIND = fold(e.target.value.trim());
    buildFacets();          // panel only — the result set is unchanged
  });
  $('#sort').addEventListener('change', (e) => { SORT = e.target.value; apply(); });
  // Wrapped, not passed directly: as a bare handler clearAll would receive the
  // Event as its `silent` argument, which is truthy, and skip the re-render.
  $('#clearBtn').addEventListener('click', () => clearAll());
  $('#clearBtn2').addEventListener('click', () => clearAll());

  // The estimate caveat only matters while estimates are actually in view.
  const banner = $('#estBanner');
  // Read the flag on every apply(), not once at boot — capturing it in a const
  // meant the next filter/sort/keystroke un-dismissed the banner.
  const isDismissed = () => localStorage.getItem('rw-banner') === 'off';
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
    banner.hidden = isDismissed()
      || SORT !== 'gap_usd_desc'
      || FILTERS.basis.size > 0;
  };

  const fb = $('#filterBtn');
  fb.addEventListener('click', () => {
    const open = fb.getAttribute('aria-expanded') === 'true';
    fb.setAttribute('aria-expanded', open ? 'false' : 'true');
    $('#panel').hidden = open;
  });

  $('#viewBtn').addEventListener('click', () => {
    setView(VIEW === 'map' ? 'list' : 'map');
    writeHash();
  });

  // --- progressive rendering -------------------------------------------
  $('#showMore').addEventListener('click', renderPage);
  // Auto-load as the button nears the viewport; the button stays as the
  // keyboard/no-IntersectionObserver path.
  if ('IntersectionObserver' in window) {
    new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && RENDERED < RESULTS.length) renderPage();
    }, { rootMargin: '600px' }).observe($('#showMore'));
  }

  // --- back to top -------------------------------------------------------
  const toTop = $('#toTop');
  toTop.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
    $('#q').focus({ preventScroll: true });
  });
  let ticking = false;
  addEventListener('scroll', () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => { toTop.hidden = scrollY < 900; ticking = false; });
  }, { passive: true });

  // --- keyboard ----------------------------------------------------------
  addEventListener('keydown', (e) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
    if (e.key === '/' && !typing) { e.preventDefault(); $('#q').focus(); return; }
    if (e.key === 'Escape') {
      if (typing && $('#q').value) { QUERY = ''; $('#q').value = ''; apply(); }
      else if (fb.getAttribute('aria-expanded') === 'true') fb.click();
    }
  });

  /** The requested view must be read BEFORE apply(), because apply() calls
   *  writeHash(), which serialises the CURRENT view and would erase a
   *  requested `view=map` before anything had a chance to act on it. */
  const wantedView = () =>
    new URLSearchParams(location.hash.replace(/^#/, '')).get('view') === 'map'
      ? 'map' : 'list';

  // Changing only the hash is a same-document navigation, so boot() does not
  // re-run. Without this, pasting or editing a filter URL on an already-open
  // page silently does nothing.
  addEventListener('hashchange', () => {
    const want = wantedView();
    clearAll(true);
    readHash();
    VIEW = want;          // so the writeHash() inside apply() preserves it
    apply();
    setView(want);        // sync the DOM and open the map if needed
  });

  VIEW = wantedView();
  apply();
  BOOTED = true;
  setView(VIEW);
}

boot();
