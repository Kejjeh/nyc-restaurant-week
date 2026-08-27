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

const DATA_DIR = 'data/';
const REGISTRY_URL = `${DATA_DIR}seasons.json`;
const PLACES_URL = `${DATA_DIR}places.json`;
/* The pre-registry payload. A published site can be a build behind this file,
   and a browser can be holding a cached index.html — either way there may be
   no seasons.json to fetch, and the single-season page must still come up. */
const LEGACY_URL = `${DATA_DIR}restaurants.json`;
/* The only season the un-namespaced localStorage keys can have belonged to:
   when they were written there was exactly one. Also the namespace used when
   no registry is there to name one. */
const LEGACY_SEASON = 'srw26';

/* ---------- helpers ----------------------------------------------------- */

const $ = (sel) => document.querySelector(sel);
/* For elements added after a release: a browser holding a cached index.html
   against a fresh app.js must degrade, not die halfway through apply(). */
const $opt = (sel) => document.querySelector(sel) || el('span');
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

/* Debug-only: ?today=YYYY-MM-DD freezes "today" — this is a noindex personal
   tool, and every date-dependent behaviour must be checkable at any date. */
const TODAY_OVERRIDE = (/[?&]today=(\d{4}-\d{2}-\d{2})\b/.exec(location.search) || [])[1] || null;
const todayISO = () => {
  if (TODAY_OVERRIDE) return TODAY_OVERRIDE;
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

/* Anything can write to localStorage; a corrupt value must not cost the whole
   page, which is what an unguarded parse at module scope would do. */
/* Every localStorage touch goes through these three. Access itself can throw
   -- a browser set to block all cookies raises on the property, not merely on
   the call -- and Safari's private mode throws on write. Unguarded, the read
   at boot took the whole page down and the write took the click with it: the
   star did nothing, because persistSaved() threw before the handler could
   repaint it. Losing persistence is the most a full disk should ever cost. */
const recall = (key) => { try { return localStorage.getItem(key); } catch { return null; } };
const store = (key, value) => {
  try { localStorage.setItem(key, value); return true; } catch { return false; }
};
const forget = (key) => { try { localStorage.removeItem(key); } catch { /* already gone */ } };

function readJSON(key, fallback) {
  try {
    const v = JSON.parse(recall(key));
    return v && typeof v === 'object' ? v : fallback;
  } catch { return fallback; }
}

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const dowOf = (iso) => new Date(`${iso}T12:00:00Z`).getUTCDay();
const addDays = (iso, n) => {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};

/* Round-trip, not just a shape match: Date.parse accepts "2026-02-30" and
   quietly rolls it to March 2, so the regex alone would pass a day that does
   not exist. Reserialising is the only check that catches it. */
const isISODate = (s) => {
  if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const d = new Date(`${s}T12:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
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
let SEASONS = [];              // registry entries, newest end first; [] = none
let SEASON = null;             // the selected entry, null when there is no registry
let PLACES = [];               // your own places — prepared once, season-agnostic
const EXPANDED = new Set();    // slugs whose detail is open

/* A row you added yourself. It is in the dataset for the address, the walk and
   the rating; it is NOT in Restaurant Week, so every field the programme owns
   (window, price, gap, menu, rank) is null on it and must read as absent
   rather than as a bad answer. Roster rows carry no `source` at all. */
const isMine = (r) => r.source === 'mine';

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
  subway: new Set(),      // route ids: '4', '6', 'N', 'L' …
  outdoor: new Set(),     // 'licensed' | 'sidewalk' | 'roadway' | 'described'
  bookableBy: null,       // ISO date — keep rows still open ON this date
  minRating: null,        // floor on the WEIGHTED Google score, not raw stars
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

/* Your own shortlist and its dates, kept in this browser — and kept PER
   SEASON: last summer's shortlist is a record of what you ate, not a starting
   point for this summer. Both are reassigned when the season changes, so
   neither can be a const. */
let STATE_SEASON = LEGACY_SEASON;
let SAVED = new Set();
let PLAN = new Map();
const persistSaved = () =>
  store(`rw-saved:${STATE_SEASON}`, JSON.stringify([...SAVED]));
const persistPlan = () =>
  store(`rw-plan:${STATE_SEASON}`, JSON.stringify(Object.fromEntries(PLAN)));

function loadState(code) {
  STATE_SEASON = code;
  SAVED = new Set(readJSON(`rw-saved:${code}`, []));
  PLAN = new Map(Object.entries(readJSON(`rw-plan:${code}`, {})));
}

/* The keys were global before there were seasons, so whatever is in them was
   written under the one season there was. Deleting the old key is the guard
   that makes this run once; an existing namespaced value is never clobbered. */
function migrateLegacyState() {
  for (const base of ['rw-saved', 'rw-plan']) {
    const old = recall(base);
    if (old == null) continue;
    const key = `${base}:${LEGACY_SEASON}`;
    // Drop the legacy key only once the copy has actually landed, or a refused
    // write would discard the shortlist it was supposed to carry over.
    if (recall(key) != null || store(key, old)) forget(base);
  }
}

let SORT = 'best';   // trust-weighted; see SORTS.best
let QUERY = '';
let BANNER = () => {};   // set during boot, once the banner nodes exist
let FACET_FIND = '';                    // "find a filter" query
const EXPANDED_FACETS = new Set();      // facets the user expanded past the cap

/* Facet groups are declared once. Adding a facet = one entry here; the panel,
   the counts, the URL state and the clear-all all follow automatically. */
const FACETS = [
  // The coarsest split there is, and only a question at all once you have
  // added something of your own — otherwise every row is the same answer.
  { key: 'list',        title: 'List',          values: (r) => [isMine(r) ? 'mine' : 'rw'],
                                                show: () => ROWS.some(isMine),
                                                labels: { rw: 'Restaurant Week', mine: 'My list' } },
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
  // By TIER, not by source. "Michelin" as one chip hid that 28 of the 42
  // Michelin-only restaurants carry nothing but The Plate — the lowest rung —
  // while reading identically to the 6 with a star.
  { key: 'recognition', title: 'Recognition',   values: (r) => [...new Set((r.recognition || []).map((x) => x.tier))] },
  // Michelin and NYT are annual guides, so they are always "current" for the
  // edition held. This facet exists for James Beard, whose honours never
  // expire but do age — a 1991 nod and a 2026 one are not the same claim.
  { key: 'era',         title: 'Award recency', values: (r) => r.recog_eras || [],
                                                labels: { current: 'This year or last', recent: 'Last 5 years',
                                                          past: 'Past decade', historic: 'Over 15 years ago' },
                                                note: 'Michelin and the NYT 100 are annual guides, so they always '
                                                    + 'count as current. Only James Beard ages here.' },
  // Seeded from the exported tag_vocabulary (= config/dish_tags.json keys), so a
  // newly configured tag that matched nothing shows "0" instead of vanishing —
  // a missing chip reads as a broken pipeline rather than as the real answer.
  // Only high-confidence matches make a restaurant FILTERABLE by a tag: the low
  // rules exist to flag "probably not the dish" (lumache the pasta, truffle the
  // oil), and a filter that returns those is answering a different question.
  // The low-confidence pill still renders on the row, tooltip and all.
  { key: 'tag',         title: 'Dish tags',     values: (r) => [...new Set((r.tags || [])
                                                  .filter((t) => t.confidence !== 'low').map((t) => t.tag))],
                                                seed: () => (DATA && DATA.tag_vocabulary) || [] },
  // Routes within a 12-minute walk. Values come straight from the data, so a
  // new line or station shows up without touching the UI.
  { key: 'subway',      title: 'Subway line',   values: (r) => Object.keys(r.subway || {}),
                                                scroll: true },
  // Deliberately NOT merged into "Dish tags". Those come from the Restaurant
  // Week PDF and mean "this is on the prix fixe". These come from the
  // restaurant's own website and mean only "they serve it" — a seafood tower
  // is a la carte and will be charged on top. Merging the two would quietly
  // turn a $165 shellfish tower into something that reads as included.
  { key: 'offsite',     title: 'On their regular menu', values: (r) =>
                                                  [...new Set((r.offsite_tags || []).map((t) => t.tag))],
                                                note: 'Found on the restaurant’s own site, NOT on the '
                                                    + 'Restaurant Week menu — these are à la carte and cost extra.' },
  // Two tiers kept apart on purpose. "Licensed" is the city's Dining Out
  // register — authoritative, but it only ever covers the public pavement and
  // roadway. Rooftops, backyards and the park venues are invisible to it, so
  // the restaurant's own blurb is carried as a separate, weaker chip. There is
  // deliberately NO "no outdoor seating" value: 495 of 645 are simply unknown.
  { key: 'outdoor',     title: 'Outdoor seating', values: (r) => {
                                                    const o = r.outdoor;
                                                    if (!o) return [];
                                                    const v = [];
                                                    if (o.licensed) v.push('licensed');
                                                    if (o.sidewalk) v.push('sidewalk');
                                                    if (o.roadway) v.push('roadway');
                                                    if (o.described && !o.licensed) v.push('described');
                                                    return v;
                                                  },
                                                labels: { licensed: 'Licensed (any)', sidewalk: 'Sidewalk café',
                                                          roadway: 'Street shed', described: 'Says so on its listing' },
                                                note: 'From NYC’s Dining Out register, which covers only the '
                                                    + 'pavement and roadway — rooftops and gardens don’t appear in it. '
                                                    + 'No chip here means unknown, not “none”.' },
  // Bands are cut on the WEIGHTED score, not the raw star average, so a 4.9
  // from 14 reviews cannot sit in the same bucket as a 4.7 from 4,000.
  { key: 'grating',     title: 'Google rating', values: (r) => {
                                                  const g = r.google; if (!g) return [];
                                                  const v = g.score;
                                                  return [v >= 4.6 ? '4.6+' : v >= 4.4 ? '4.4-4.6'
                                                        : v >= 4.2 ? '4.2-4.4' : v >= 4.0 ? '4.0-4.2' : 'under 4.0'];
                                                },
                                                note: 'Weighted: a rating resting on few reviews is pulled toward '
                                                    + 'the average, so it cannot outrank a strong rating from thousands.' },
  { key: 'greviews',    title: 'Review volume', values: (r) => {
                                                  const g = r.google; if (!g) return [];
                                                  const v = g.reviews;
                                                  return [v >= 2000 ? '2,000+' : v >= 500 ? '500-2,000'
                                                        : v >= 100 ? '100-500' : 'under 100'];
                                                } },
  { key: 'menu',        title: 'Menu PDF',      values: (r) => [r.menu_state || 'none'],
                                                labels: { pdf: 'Readable PDF', image_only: 'Image-only PDF', none: 'No menu published' } },
];

/* Make the promise above literally true: a facet declared without a matching
   FILTERS entry used to throw inside buildFacets and silently render NO
   filter panel at all — the failure looked like a data problem, not a typo. */
for (const f of FACETS) if (!FILTERS[f.key]) FILTERS[f.key] = new Set();

const RECOG_LABEL = { michelin: 'Michelin', james_beard: 'James Beard', nyt: 'NYT' };
const RUBRIC_LABEL = {
  award: 'Award value (aged)', rating: 'Google rating (weighted)',
  lex: 'Walk to the 4/5/6', window: 'Days left to book',
};
const ERA_LABEL = { current: 'This year or last', recent: 'Last 5 years',
                    past: 'Past decade', historic: 'Over 15 years ago' };
const MICHELIN_TIERS = ['Michelin star', 'Michelin Bib Gourmand', 'Michelin Plate'];
const TOP_TIERS = ['Michelin star', 'NYT 100 Best', 'James Beard winner'];
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

  if (FILTERS.minRating != null) {
    // Deliberately the opposite of bookableBy above: "an unprinted window is
    // not a closed one", but an unrated restaurant genuinely cannot be shown
    // to meet a rating floor. The count of rows dropped this way is surfaced
    // on the chip so the exclusion is never silent.
    if (!r.google || r.google.score == null) return false;
    if (r.google.score < FILTERS.minRating) return false;
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

/** Walk minutes to the SELECTED lines, or to the nearest station if none are
 *  selected. Picking "6" then sorting by walk answers "closest on the 6". */
function walkMin(r) {
  const sel = [...FILTERS.subway];
  if (sel.length) {
    const v = sel.map((k) => r.subway && r.subway[k]).filter((x) => x != null);
    return v.length ? Math.min(...v) : null;
  }
  return r.subway_nearest ? r.subway_nearest.min : null;
}

/* Outdoor seating, phrased so the two tiers never blur together — and so the
   absent case reads as "we don't know", which is the truth for 495 of 645. */
function outdoorText(r) {
  const o = r.outdoor;
  if (!o) return 'Not established';
  const kinds = [];
  if (o.sidewalk) kinds.push('sidewalk café');
  if (o.roadway) kinds.push('street shed');
  if (o.licensed) {
    const where = kinds.join(' + ');
    return `Yes — licensed ${where} (city register${
      o.described ? ', and mentioned on its listing' : ''})`;
  }
  return 'Mentioned on its listing — not in the city register '
       + '(which covers only pavement and roadway)';
}

/* nulls always sort last, in every direction */
const cmpNullLast = (a, b, dir) => {
  const an = a == null, bn = b == null;
  if (an && bn) return 0;
  if (an) return 1;
  if (bn) return -1;
  return a < b ? -dir : a > b ? dir : 0;
};

/* How much the number can be trusted, not how big it is. A verified $18 is a
   better thing to show first than a heuristic $79 computed off a scraped price
   list at a restaurant's cheapest tier. */
const BASIS_RANK = { verified: 0, estimate: 1 };
const trust = (r) => (r.rank != null ? -1 : BASIS_RANK[r.gap_basis] ?? 2);

const SORTS = {
  // The default. Trust tier first, then size within the tier — so the ranked
  // picks and hand-verified gaps lead, and the estimates keep their own
  // ordering underneath instead of colonising the top of the page.
  rubric_desc: (a, b) => cmpNullLast(a.rubric, b.rubric, -1)
    || a._name.localeCompare(b._name),
  google_desc: (a, b) => cmpNullLast(a.google && a.google.score, b.google && b.google.score, -1)
    || a._name.localeCompare(b._name),
  google_raw_desc: (a, b) => cmpNullLast(a.google && a.google.rating, b.google && b.google.rating, -1)
    || ((b.google && b.google.reviews) || 0) - ((a.google && a.google.reviews) || 0)
    || a._name.localeCompare(b._name),
  distinction: (a, b) => cmpNullLast(a.recog_rank, b.recog_rank, 1)
    || (b.recog_top?.year || 0) - (a.recog_top?.year || 0)
    || a._name.localeCompare(b._name),
  best: (a, b) => trust(a) - trust(b)
    || (a.rank != null && b.rank != null ? a.rank - b.rank : 0)
    || cmpNullLast(a.gap_usd, b.gap_usd, -1)
    || a._name.localeCompare(b._name),
  gap_usd_desc: (a, b) => cmpNullLast(a.gap_usd, b.gap_usd, -1) || a._name.localeCompare(b._name),
  gap_pct_desc: (a, b) => cmpNullLast(a.gap_pct, b.gap_pct, -1) || a._name.localeCompare(b._name),
  price_asc:    (a, b) => cmpNullLast(a.rw_price, b.rw_price, 1) || a._name.localeCompare(b._name),
  price_desc:   (a, b) => cmpNullLast(a.rw_price, b.rw_price, -1) || a._name.localeCompare(b._name),
  end_asc:      (a, b) => cmpNullLast(a.end_date, b.end_date, 1) || a._name.localeCompare(b._name),
  end_desc:     (a, b) => cmpNullLast(a.end_date, b.end_date, -1) || a._name.localeCompare(b._name),
  walk_asc:     (a, b) => cmpNullLast(walkMin(a), walkMin(b), 1) || a._name.localeCompare(b._name),
  name_asc:     (a, b) => a._name.localeCompare(b._name),
  rank_asc:     (a, b) => cmpNullLast(a.rank, b.rank, 1) || a._name.localeCompare(b._name),
};

/* Links come from the listing API and, for your own places, from a file you
   type by hand -- and a bare "www.joesbar.com" in an href is a RELATIVE path,
   so the button would quietly navigate off the dashboard to a 404 instead of
   to the restaurant. Anything that is not an absolute http(s) URL is dropped
   rather than rendered as a link that lies about where it goes.
   No base is passed to URL() on purpose: with one, "www.joesbar.com" resolves
   against this page and comes back with an http: protocol -- passing the very
   check it needs to fail. Without one, a relative string throws, which is the
   answer we want. */
const isHttpURL = (u) => {
  if (!u) return false;
  try { return /^https?:$/.test(new URL(u).protocol); } catch { return false; }
};

/* ---------- rendering --------------------------------------------------- */

function pill(cls, text, title) {
  const p = el('span', `pill ${cls}`, text);
  if (title) p.title = title;
  return p;
}

/* Urgency is "closing within a week from wherever we are now", not "on or
   before the program's headline date". Frozen against book_by, every closed
   restaurant would keep shouting BOOK BY once that date passed. */
function urgencyHorizon() {
  const h = addDays(todayISO(), 7);
  return DATA.program_end && h > DATA.program_end ? DATA.program_end : h;
}

/* Where the season is, relative to now. Past program_end nothing can be booked
   at all, and copy written for a live programme ("days left", "closing soon",
   "no dates left") stops being true — 'archive' is what switches it. */
function seasonPhase() {
  // The registry's `status` is only as fresh as that season's own last build,
  // so "live" proves nothing and the dates below decide. "archived" is the one
  // direction a stale stamp cannot be wrong in: it never un-ends.
  if (SEASON && SEASON.status === 'archived') return 'archive';
  const t = todayISO();
  if (DATA.program_end && t > DATA.program_end) return 'archive';
  if (DATA.book_by && t > DATA.book_by) return 'extensions';
  return 'core';
}

function hasEnded(r) {
  return !!(r.end_date && r.end_date < todayISO());
}

function isUrgent(r) {
  return !!(r.end_date && r.end_date >= todayISO() && r.end_date <= urgencyHorizon());
}

/* Spans, not divs: this cell is rendered inside the row's <button> header. */
function renderGapCell(r) {
  const cell = el('span', 'gapCell');
  // Your own place was never priced against a Restaurant Week menu, so it has
  // no gap — and "no comparable" would be answering a question never asked
  // of it. The cell stays empty and keeps the column aligned.
  if (isMine(r)) return cell;
  if (r.gap_basis === 'estimate') cell.classList.add('est');

  if (r.gap_usd == null) {
    cell.append(el('span', 'gapNone', '—'));
    cell.append(el('span', 'gapLabel', 'no comparable'));
    return cell;
  }

  // A negative gap means RW costs MORE than the comparable. 38 estimates are
  // negative; rendering "$-11 / -22% off" would read as a discount.
  const over = r.gap_usd < 0;
  if (over) cell.classList.add('over');

  let usd = money(Math.abs(r.gap_usd));
  if (r.gap_usd_high != null && r.gap_usd_high !== r.gap_usd) usd += `–${Math.abs(r.gap_usd_high)}`;
  cell.append(el('span', 'gapUsd', (over ? '+' : '') + usd));

  if (r.gap_pct != null) {
    let pct = `${Math.abs(r.gap_pct)}%`;
    if (r.gap_pct_high != null && r.gap_pct_high !== r.gap_pct) {
      pct = `${Math.abs(r.gap_pct)}–${Math.abs(r.gap_pct_high)}%`;
    }
    cell.append(el('span', 'gapPct', pct + (over ? ' MORE' : ' off')));
  }
  cell.append(el('span', 'gapLabel', r.gap_basis === 'estimate' ? 'estimate' : 'verified'));
  return cell;
}

function renderRow(r) {
  const row = el('article', 'row');
  row.dataset.slug = r.slug;
  if (r.rank != null) row.classList.add('ranked');
  const urgent = isUrgent(r);
  if (urgent) row.classList.add('urgent');

  // Everything inside a <button> is spans, never divs: flow content in a button
  // is invalid, and the assistive tech that tolerates it flattens the whole
  // header into one unpunctuated run. The grid/flex CSS blockifies them anyway,
  // so this is a change of element, not of layout.
  const head = el('button', 'rowHead');
  head.type = 'button';
  head.setAttribute('aria-expanded', EXPANDED.has(r.slug) ? 'true' : 'false');

  const main = el('span', 'rowMain');

  const nameLine = el('span', 'nameLine');
  if (r.rank != null) nameLine.append(el('span', 'rankBadge', `#${r.rank}`));
  nameLine.append(el('span', 'rname', r.name));
  main.append(nameLine);

  const meta = el('span', 'metaLine');
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
  // Your own places carry no borough, neighborhood or cuisine — only what you
  // typed and what Google returned. Without this the line renders blank.
  if (isMine(r) && !meta.childNodes.length && r.address) {
    meta.append(el('span', null, r.address));
  }
  main.append(meta);

  const pills = el('span', 'pills');
  if (SAVED.has(r.slug)) pills.append(pill('saved', '★ saved'));
  if (isMine(r)) {
    // No window, so nothing here is urgent, ended, or a missing end date —
    // "no end date" would report a data gap where there is no datum to have.
    pills.append(pill('quiet', 'my list',
      'On your own list — not a Restaurant Week participant'));
  } else if (urgent) {
    // Show the row's OWN closing date, not the program-wide Aug 16 headline —
    // 18 rows close earlier than that, and a tooltip is not a disclosure.
    pills.append(pill('crit', `book by ${fmtDate(r.end_date)}`,
      r.end_date_source === 'conflict'
        ? 'Two verified sources disagree — the earlier date is shown'
        : `RW window ends ${fmtDate(r.end_date)}`));
  } else if (hasEnded(r)) {
    pills.append(pill('quiet', `ended ${fmtDate(r.end_date)}`,
      'This restaurant’s Restaurant Week window has closed'));
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
    const grp = (r.recognition || []).filter((y) => `${y.source}|${y.level}` === key);
    const n = grp.length;
    // Badges arrive strongest-then-newest, so x is the freshest of its group.
    // Michelin and NYT are annual guides — the edition year is implied by
    // being in it at all. James Beard is an event, so it gets dated: a '99
    // semifinalist and a '26 winner should never read the same.
    const dated = x.source === 'james_beard' && x.year;
    const aged = x.era === 'past' || x.era === 'historic';
    pills.append(pill(`rec${aged ? ' aged' : ''}`,
      `${x.tier || RECOG_LABEL[x.source] || x.source}${dated ? ` ’${String(x.year).slice(2)}` : ''}${n > 1 ? ` ×${n}` : ''}`,
      `${x.matched_name || r.name}${x.year ? ` · ${x.year}` : ''}`
      + `${x.era ? ` · ${ERA_LABEL[x.era]}` : ''}`
      + `${x.name_match_only ? ' · name match only' : ''}`));
  });
  /* Places close between seasons and the listing never catches up. Say which
     kind, because they are different facts and only one of them means don't
     bother: `closed` is PERMANENT only, and a temporary closure is quoted as
     temporary. This pill used to read "permanently closed" for anything Google
     did not call OPERATIONAL — including CLOSED_TEMPORARILY, i.e. the opposite
     of what the tooltip credited Google with saying. */
  if (r.google && r.google.closed) {
    pills.append(pill('warn', 'permanently closed',
      'Google reports this location as permanently closed'));
  } else if (r.google && r.google.status === 'CLOSED_TEMPORARILY') {
    pills.append(pill('warn', 'temporarily closed',
      'Google reports this location as temporarily closed — it has not shut '
      + 'for good, but check before you travel'));
  }
  if (r.google && r.google.rating != null) {
    const g = r.google;
    pills.append(pill('quiet',
      g.rating.toFixed(1) + '\u2605 ' + g.reviews.toLocaleString(),
      'Google: ' + g.rating + ' from ' + g.reviews.toLocaleString() + ' reviews'
      + ' \u00b7 weighted to ' + g.score.toFixed(2) + ' for sorting'));
  }
  (r.tags || []).forEach((t, i, arr) => {
    if (arr.findIndex((z) => z.tag === t.tag) !== i) return; // dedupe by tag
    pills.append(pill('tag', t.tag, t.confidence === 'low' ? 'Low-confidence match' : 'Dish tag match'));
  });
  if (pills.childNodes.length) main.append(pills);

  head.append(main, renderGapCell(r));
  head.addEventListener('click', () => toggleDetail(r, row, head));
  row.append(head);

  if (EXPANDED.has(r.slug)) row.append(openDetail(r, head));
  return row;
}

/* The detail is a SIBLING of the button that discloses it, so aria-expanded
   alone leaves no trail from one to the other. The pointer is set only while
   the panel exists — toggleDetail removes the node rather than hiding it, and
   aria-controls must never name an id that is not in the document. */
function openDetail(r, head) {
  const d = renderDetail(r);
  d.id = `detail-${r.slug}`;
  head.setAttribute('aria-controls', d.id);
  return d;
}

function toggleDetail(r, row, head) {
  const open = EXPANDED.has(r.slug);
  if (open) {
    EXPANDED.delete(r.slug);
    const d = row.querySelector('.detail');
    if (d) d.remove();
    head.setAttribute('aria-expanded', 'false');
    head.removeAttribute('aria-controls');
  } else {
    EXPANDED.add(r.slug);
    row.append(openDetail(r, head));
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
  book_by_aug16: () => `Ends ${fmtDate(DATA.book_by)} or sooner — book first`,
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

/** Flag copy, resolved late so season dates come from the payload, not source. */
const flagText = (f) =>
  (typeof FLAG_TEXT[f] === 'function' ? FLAG_TEXT[f]() : FLAG_TEXT[f]);

function renderDetail(r) {
  const d = el('section', 'detail');

  const mine = isMine(r);
  if (mine) {
    const n = el('div', 'note');
    n.append(el('strong', null, 'Your list '));
    n.append(document.createTextNode(
      'Not a Restaurant Week participant, so it has no window, no prix fixe '
      + 'price and no value gap. What is below is the address, the walk and '
      + 'the rating — everything else about it is unknown to this dataset.'));
    d.append(n);
  }
  if (r.verdict_note) {
    const n = el('div', 'note');
    n.append(el('strong', null, r.grade ? `Verdict [${r.grade}] ` : 'Verdict '));
    n.append(document.createTextNode(r.verdict_note));
    d.append(n);
  }
  if (r.notes) {
    // On a roster row `notes` is a caveat found during verification; on your
    // own row it is the note YOU wrote, which is not a warning about anything.
    const n = el('div', mine ? 'note' : 'note warn');
    n.append(el('strong', null, mine ? 'Note ' : 'Caveat '));
    n.append(document.createTextNode(r.notes));
    d.append(n);
  }
  (r.flags || []).forEach((f) => {
    if (!FLAG_TEXT[f]) return;
    // The headline-deadline flag is already a pill on the row while it is live,
    // and says nothing once that deadline is behind us.
    if (f === 'book_by_aug16') return;
    const n = el('div', 'note warn');
    n.append(document.createTextNode(flagText(f)));
    d.append(n);
  });

  const dl = el('dl', 'dgrid');
  /* Fields only the programme — or a pipeline that only runs over it — can
     fill. Several of them print a default ("Not stated", "Not established",
     "No RW menu published") when the value is null, and on your own row every
     one of those defaults is a claim about a restaurant nobody ever checked. */
  const rwField = (label, value, mono) => { if (!mine) field(dl, label, value, mono); };
  rwField('RW price', r.rw_price != null
    ? money(r.rw_price) + (r.price_source === 'verified' ? ' (verified)' : '')
    : (r.price_tiers || []).join(' / ') || null, true);
  rwField('Comparable', r.comparable_usd != null
    ? money(r.comparable_usd) + (r.comparable_usd_high ? `–${r.comparable_usd_high}` : '')
    : null, true);
  // Name the tier the estimate was computed against — the gap is always taken
  // at ONE tier, and without saying which, the comparable minus the headline
  // price appears not to add up.
  rwField('Estimated at tier', r.estimate_tier || null, true);
  rwField('Gap basis', r.gap_basis === 'verified' ? 'Verified — own-menu arithmetic'
    : r.gap_basis === 'estimate'
      ? `Heuristic estimate — triage only (${r.estimate_confidence || '?'} confidence)`
      : 'No comparable published');
  rwField('Window ends', r.end_date
    ? `${fmtDate(r.end_date)}${r.end_date_source === 'printed' ? ' (printed)' : r.end_date_source === 'conflict' ? ' (conservative)' : ' (listing API)'}`
    : 'Not stated', true);
  rwField('Days / service', r.days);
  // "verified" only when it actually came from the restaurant. 160 rows are
  // false purely because the listing API says so — that is not verification.
  rwField('Sunday',
    r.sunday === true
      ? (r.sunday_source === 'verified' ? 'Yes — verified' : 'Yes — per listing (unverified)')
      : r.sunday === false
        ? (r.sunday_source === 'verified' ? 'No — verified' : 'No — per listing (unverified)')
        : 'Not established');
  rwField('Courses', r.courses != null ? `${r.courses}-course` : null);
  field(dl, 'Meal periods', (r.meal_periods || []).join(', ') || null);
  field(dl, 'Price tiers', (r.price_tiers || []).join(' / ') || null, true);
  field(dl, 'Cuisines', (r.cuisines || []).join(', ') || null);
  if (r.subway_nearest) {
    field(dl, 'Nearest subway',
      `${r.subway_nearest.name} — ${r.subway_nearest.routes.join(' ')} · ~${r.subway_nearest.min} min walk`);
  }
  const lex = ['4', '5', '6'].map((k) => r.subway && r.subway[k]).filter((v) => v != null);
  if (lex.length) field(dl, 'Walk to 4/5/6', `~${Math.min(...lex)} min`, true);
  field(dl, 'Google rating', r.google && r.google.rating != null
    ? r.google.rating + ' from ' + r.google.reviews.toLocaleString() + ' reviews'
      + ' \u00b7 weighted ' + r.google.score.toFixed(2)
    : null, true);
  field(dl, 'Google status', !r.google ? null
    : r.google.closed
      ? 'Reported permanently closed \u2014 the rating above is what it closed with'
    : r.google.status === 'CLOSED_TEMPORARILY'
      ? 'Reported temporarily closed \u2014 not shut for good; check before you travel'
      : null);
  rwField('Outdoor seating', outdoorText(r));
  field(dl, 'Address', r.address || (r.borough ? `${r.borough} — address unavailable` : null));
  rwField('Final-list rank', r.rank != null ? `#${r.rank} of 15` : null, true);
  rwField('Menu', r.menu_state === 'pdf' ? 'Official PDF published'
    : r.menu_state === 'image_only' ? 'PDF is image-only — not machine-readable'
    : 'No RW menu published');
  d.append(dl);

  if ((r.recognition || []).length) {
    const s = el('div', 'dsec');
    s.append(el('h4', null, 'Recognition'));
    const ul = el('ul', 'snips');
    r.recognition.forEach((x) => {
      const li = el('li', 'snip');
      const label = `${x.tier || RECOG_LABEL[x.source] || x.source}`
        + `${x.year ? ` (${x.year})` : ''}`
        + `${x.era && x.era !== 'current' ? ` — ${ERA_LABEL[x.era].toLowerCase()}` : ''}`;
      if (isHttpURL(x.url)) {
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

  if (r.rubric != null) {
    const s2 = el('div', 'dsec');
    s2.append(el('h4', null, `Rubric grade — ${r.rubric.toFixed(1)}`));
    s2.append(el('p', 'dnote',
      r.rubric_completeness < 100
        ? `Built on ${r.rubric_completeness}% real data. ${(r.rubric_imputed || []).length
            ? (r.rubric_imputed.map((k) => RUBRIC_LABEL[k] || k).join(' and ')
               + ` unknown, so scored at the field average rather than penalised.`)
            : ''}`
        : 'Every component had real data behind it.'));
    const tbl = el('div', 'rubricBars');
    Object.keys(DATA.rubric_weights).forEach((k) => {
      const v = r.rubric_parts[k];
      const row = el('div', 'rbRow');
      row.append(el('span', 'rbK', `${RUBRIC_LABEL[k] || k} ·${DATA.rubric_weights[k]}`));
      const track = el('span', 'rbTrack');
      if (v == null) {
        // No fill at all. An unset width resolves to 100%, which rendered a
        // FULL bar for a component we know nothing about -- missing data
        // reading as a perfect score, which is the exact inversion this
        // whole rubric is built to avoid.
        row.classList.add('unknown');
      } else {
        const fill = el('span', 'rbFill', '');
        fill.style.width = `${v}%`;
        track.append(fill);
      }
      row.append(track);
      row.append(el('span', 'rbV', v == null ? 'not known' : String(Math.round(v))));
      tbl.append(row);
    });
    s2.append(tbl);
    d.append(s2);
  }

  if ((r.offsite_tags || []).length) {
    const s = el('div', 'dsec');
    s.append(el('h4', null, 'On their regular menu'));
    // Said once per restaurant, in the row itself: the facet note is easy to
    // miss, and this is the difference between "included" and "$165 extra".
    s.append(el('p', 'dnote', 'From the restaurant’s own site — not part of the '
      + 'Restaurant Week menu, so it is charged separately.'));
    const ul = el('ul', 'snips');
    r.offsite_tags.forEach((t) => {
      const li = el('li', 'snip');
      li.append(pill('tag', t.tag));
      if (t.confidence === 'verified') li.append(pill('value', 'checked by hand'));
      else if (t.confidence === 'low') li.append(pill('warn', 'low confidence'));
      // A hand-checked row carries what it actually is and what it costs — the
      // whole point of the check was to replace a keyword with the real item.
      if (t.item) {
        li.append(el('strong', 'offItem',
          t.price_usd != null ? `${t.item} — $${t.price_usd}` : t.item));
      }
      if (t.snippet) li.append(el('q', null, t.snippet));
      if (isHttpURL(t.url)) {
        const a = el('a', 'snipSrc', 'source');
        a.href = t.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
        li.append(a);
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
    if (!SAVED.has(r.slug)) { PLAN.delete(r.slug); persistPlan(); }
    paint();
    // Repaint the row's pills and the Saved preset count without collapsing
    // the panel the user is reading.
    buildPresets();
    syncCompareBtn();
    const pills = document.querySelector(`.row[data-slug="${CSS.escape(r.slug)}"] .pills`);
    if (pills) {
      const star = pills.querySelector('.pill.saved');
      if (SAVED.has(r.slug) && !star) pills.prepend(pill('saved', '★ saved'));
      if (!SAVED.has(r.slug) && star) star.remove();
    }
  });
  links.append(save);
  const addLink = (href, text, primary) => {
    if (!isHttpURL(href)) return;
    const a = el('a', `linkBtn${primary ? ' primary' : ''}`, text);
    a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
    links.append(a);
  };
  addLink(r.links && r.links.reservation, 'Book a table', true);
  addLink(r.links && r.links.menu, r.menu_state === 'image_only' ? 'Menu PDF (image-only)' : 'Menu PDF');
  addLink(r.links && r.links.listing, 'Official listing');
  addLink(r.links && r.links.website, 'Restaurant site');

  // A link to THIS restaurant. Filter state is shareable but a single
  // restaurant was not, which is the first thing you reach for when you want
  // to send someone a recommendation.
  const share = el('button', 'linkBtn', 'Copy link');
  share.type = 'button';
  share.addEventListener('click', async () => {
    const url = `${location.origin}${location.pathname}#r=${encodeURIComponent(r.slug)}`;
    try {
      await navigator.clipboard.writeText(url);
      share.textContent = 'Link copied';
    } catch {
      // clipboard is blocked over http:// and in some embedded views
      share.textContent = url;
      share.style.userSelect = 'all';
    }
    setTimeout(() => { share.textContent = 'Copy link'; share.style.userSelect = ''; }, 2500);
  });
  links.append(share);

  if (links.childNodes.length) d.append(links);

  return d;
}

/** Open one restaurant from #r=slug, lifting any filter that would hide it. */
function openRestaurant(slug) {
  const r = ROWS.find((x) => x.slug === slug);   // slug "53" stays a string
  if (!r) return false;
  if (!RESULTS.some((x) => x.slug === slug)) {
    clearAll(true);            // the link must win over whatever was filtered
    FILTERS.bookableBy = null;
    FILTERS.minRating = null;
  }
  EXPANDED.add(slug);
  VIEW = 'list';
  // Drop `r` from the hash: writeHash() refuses to run while it is there, so
  // leaving it would freeze filter/sort/view state for the rest of the session.
  const p = new URLSearchParams(location.hash.replace(/^#/, ''));
  p.delete('r');
  history.replaceState(null, '', p.toString() ? `#${p}` : location.pathname + location.search);
  apply();
  // it may sit past the first page of 50 — keep rendering until it exists
  let guard = 0;
  while (!document.querySelector(`.row[data-slug="${CSS.escape(slug)}"]`)
         && RENDERED < RESULTS.length && guard++ < 40) {
    renderPage();
  }
  const row = document.querySelector(`.row[data-slug="${CSS.escape(slug)}"]`);
  if (row) {
    row.scrollIntoView({ block: 'center' });
    row.classList.add('linked');
    setTimeout(() => row.classList.remove('linked'), 2400);
  }
  return true;
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
    // A facet that has nothing to ask about is not rendered as an empty group.
    if (f.show && !f.show()) continue;
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
    if (f.note) sec.append(el('p', 'facetNote', f.note));
    const box = el('div', f.scroll ? 'scroller' : null);
    const chips = el('div', 'chips');

    // Long tails (76 neighborhoods, 56 cuisines) are collapsed to the most
    // populated values; selected ones always stay visible so they can be
    // turned off. Searching or expanding reveals the rest.
    const CAP = 12;
    const capped = !FACET_FIND && !EXPANDED_FACETS.has(f.key) && entries.length > CAP + 3;
    let hiddenCount = 0;
    if (capped) {
      const keep = entries.slice(0, CAP);
      const sel = entries.slice(CAP).filter(([v]) => FILTERS[f.key].has(v));
      // Selected values past the cap are put back, so they are no longer among
      // the hidden — counting before the concat promised more than "Show more"
      // could deliver.
      hiddenCount = entries.length - CAP - sel.length;
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
      const more = el('button', 'moreLink');
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
  const rsec = el('div', 'facet');
  rsec.append(el('h3', null, 'Minimum rating'));
  rsec.append(el('p', 'facetNote',
    'On the weighted score, so a floor of 4.4 is not met by a 4.9 resting on '
    + '14 reviews. Restaurants with no rating are excluded while this is set.'));
  const rwrap = el('div', 'dateFacet');
  [null, 4.0, 4.2, 4.4, 4.6].forEach((v) => {
    const b = el('button', 'chip', v == null ? 'Any rating' : `${v.toFixed(1)}+`);
    b.type = 'button';
    b.setAttribute('aria-pressed', FILTERS.minRating === v ? 'true' : 'false');
    if (v != null) {
      b.append(el('span', 'c', String(ROWS.filter((r) =>
        r.google && r.google.score != null && r.google.score >= v).length)));
    }
    b.addEventListener('click', () => { FILTERS.minRating = v; apply(); });
    rwrap.append(b);
  });
  rsec.append(rwrap);
  host.append(rsec);

  const sec = el('div', 'facet');
  sec.append(el('h3', null, 'Still bookable on'));
  const wrapEl = el('div', 'dateFacet');
  const input = el('input');
  input.type = 'date';
  input.id = 'bookableBy';
  // The <h3> above names this group, not the control — without a label of its
  // own the input announces as a bare "date".
  const lab = el('label', 'sr-only', 'Still bookable on');
  lab.htmlFor = input.id;
  wrapEl.append(lab);
  // Bounded by the season itself: no window opens before it starts or survives
  // the programme's end, so a date outside them can only return a wrong answer.
  if (DATA.season_start) input.min = DATA.season_start;
  if (DATA.program_end) input.max = DATA.program_end;
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
    // Only appears once you have added a place of your own.
    key: 'mine', label: 'My list',
    show: () => ROWS.some(isMine),
    count: () => ROWS.filter(isMine).length,
    is: () => FILTERS.list.has('mine'),
    set() { FILTERS.list.add('mine'); },
  },
  {
    key: 'ranked', label: 'The 15',
    count: () => ROWS.filter((r) => r.rank != null).length,
    is: () => FILTERS.verdict.has('Ranked pick'),
    set() { FILTERS.verdict.add('Ranked pick'); SORT = 'rank_asc'; },
  },
  {
    key: 'urgent', label: 'Closing soon',
    // Nothing closes once everything has closed.
    show: () => seasonPhase() !== 'archive',
    count: () => ROWS.filter(isUrgent).length,
    is: () => FILTERS.endingBy === urgencyHorizon() && FILTERS.bookableBy === todayISO(),
    set() {
      FILTERS.bookableBy = todayISO();
      FILTERS.endingBy = urgencyHorizon();
      SORT = 'end_asc';
    },
  },
  {
    key: 'verified', label: 'Verified gaps',
    count: () => ROWS.filter((r) => r.gap_basis === 'verified').length,
    is: () => FILTERS.basis.has('verified'),
    set() { FILTERS.basis.add('verified'); SORT = 'gap_usd_desc'; },
  },
  {
    key: 'lex', label: '4/5/6',
    count: () => ROWS.filter((r) => ['4', '5', '6'].some((k) => r.subway && r.subway[k] != null)).length,
    is: () => ['4', '5', '6'].every((k) => FILTERS.subway.has(k)),
    set() { ['4', '5', '6'].forEach((k) => FILTERS.subway.add(k)); SORT = 'walk_asc'; },
  },
  {
    key: 'sunday', label: 'Sunday',
    count: () => ROWS.filter((r) => r.sunday === true).length,
    is: () => FILTERS.sunday.has('yes'),
    set() { FILTERS.sunday.add('yes'); },
  },
  {
    // The recognition facet holds TIERS now, so this preset has to name all
    // three Michelin rungs — adding the bare source would match nothing.
    key: 'michelin', label: 'Michelin',
    count: () => ROWS.filter((r) => (r.recognition || []).some((x) => x.source === 'michelin')).length,
    is: () => MICHELIN_TIERS.every((t) => FILTERS.recognition.has(t)),
    set() { MICHELIN_TIERS.forEach((t) => FILTERS.recognition.add(t)); },
  },
  {
    // The point of ranking distinctions: one click for the genuinely top ones,
    // rather than a "Michelin" chip that is 28/42 the lowest rung.
    key: 'toprec', label: 'Top awards',
    count: () => ROWS.filter((r) => (r.recognition || []).some((x) => TOP_TIERS.includes(x.tier))).length,
    is: () => TOP_TIERS.every((t) => FILTERS.recognition.has(t)),
    set() { TOP_TIERS.forEach((t) => FILTERS.recognition.add(t)); SORT = 'distinction'; },
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
  if (FILTERS.minRating != null) {
    const unrated = ROWS.filter((r) => !r.google || r.google.score == null).length;
    add('Rating', `${FILTERS.minRating.toFixed(1)}+`
      + (unrated ? ` · ${unrated} unrated hidden` : ''),
      () => { FILTERS.minRating = null; });
  }
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
  if (FILTERS.minRating != null) n += 1;
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
    const ended = key < todayISO();
    const urgent = !ended && key <= urgencyHorizon();
    if (urgent) h.classList.add('urgent');
    h.append(el('h2', null,
      `${ended ? 'Closed' : urgent ? 'Closes' : 'Runs through'} ${fmtDate(key)}`));
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
  const cmp = SORTS[SORT] || SORTS.best;
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
  $('#urgentCount').textContent = urgent ? `· ${urgent} ending by ${fmtDate(urgencyHorizon())}` : '';
  $('#empty').hidden = RESULTS.length !== 0;
  // A date past the last window can never match, whatever else is set, so
  // "nothing matches those filters" would send you loosening the wrong ones.
  const deadDate = FILTERS.bookableBy && DATA.program_end
    && FILTERS.bookableBy > DATA.program_end;
  $opt('#emptyMsg').textContent = deadDate
    ? `No window reached ${fmtDate(FILTERS.bookableBy)} — the season ended `
      + `${fmtDate(DATA.program_end)}. Clear the date filter to see the full season.`
    : 'Nothing matches those filters.';

  const n = activeCount();
  const badge = $('#filterCount');
  badge.textContent = String(n);
  badge.hidden = n === 0;
  $('#clearBtn').hidden = n === 0;

  if (VIEW === 'map') drawMarkers();
  if (VIEW === 'stats') renderStats();
  if (VIEW === 'compare') renderCompare();
  syncCompareBtn();
  if (VIEW === 'plan') renderPlan();

  buildPresets();
  buildActiveFilters();

  buildFacets();
  BANNER();
  writeHash();
}

/* Typing is the only control that fires per keystroke, and every keystroke ran
   the whole of apply(): re-filter 636 rows, re-count nineteen facets against
   nineteen separate passes, rebuild ~100 chips. "steakhouse" cost ten of them.
   Coalesce a burst into the one pass its last letter deserves.
   The facet counts still have to be rebuilt: matches() tests QUERY ahead of the
   facet loop and `exceptFacet` excludes only a facet's OWN selection, so every
   chip count is query-dependent — leaving them behind would make each one a
   claim about a result set that is no longer on screen. */
const QUERY_WAIT = 130;
let QUERY_T = 0;
function queueQuery() {
  clearTimeout(QUERY_T);
  QUERY_T = setTimeout(() => {
    // Read the box rather than the keystroke that queued this. Escape, Clear
    // filters and a season switch all empty the box and set QUERY themselves,
    // and a stale letter landing behind them would leave the two disagreeing.
    const q = fold($('#q').value.trim());
    if (q === QUERY) return;
    QUERY = q;
    apply();
  }, QUERY_WAIT);
}

/** `silent` resets state without re-rendering — presets clear then set, and
 *  would otherwise render an empty intermediate view. */
function clearAll(silent) {
  FACETS.forEach((f) => FILTERS[f.key].clear());
  FILTERS.bookableBy = null;
  FILTERS.minRating = null;
  FILTERS.endingBy = null;
  FILTERS.savedOnly = false;
  QUERY = '';
  $('#q').value = '';
  if (!silent) apply();
}

/* ---------- URL state (so a filtered view survives a reload / bookmark) -- */

function writeHash() {
  if (new URLSearchParams(location.hash.replace(/^#/, '')).get('r')) return;
  const p = new URLSearchParams();
  // Only written once there is a choice to record, so a single-season site's
  // URLs stay exactly what they were before the registry existed.
  if (SEASONS.length > 1 && SEASON) p.set('season', SEASON.code);
  FACETS.forEach((f) => { if (FILTERS[f.key].size) p.set(f.key, [...FILTERS[f.key]].join('~')); });
  // "any" is written explicitly: an absent `by` means "no state yet", which
  // boot resolves to today — so clearing the date filter has to be recorded,
  // or a reload silently re-applies today's date.
  p.set('by', FILTERS.bookableBy || 'any');
  if (FILTERS.minRating != null) p.set('minr', String(FILTERS.minRating));
  if (FILTERS.endingBy) p.set('to', FILTERS.endingBy);
  if (FILTERS.savedOnly) p.set('saved', '1');
  if (VIEW !== 'list') p.set('view', VIEW);
  if (QUERY) p.set('q', QUERY);
  if (SORT !== 'best') p.set('sort', SORT);
  const s = p.toString();
  history.replaceState(null, '', s ? `#${s}` : location.pathname + location.search);
}

/** Every value a facet could legitimately hold, from the loaded roster plus
 *  whatever the facet seeds itself with. */
function knownFacetValues(f) {
  const out = new Set((f.seed ? f.seed() : []).map(String));
  ROWS.forEach((r) => f.values(r).forEach((v) => out.add(String(v))));
  return out;
}

function readHash() {
  const raw = location.hash.replace(/^#/, '');
  if (!raw) return;
  const p = new URLSearchParams(raw);
  FACETS.forEach((f) => {
    const v = p.get(f.key);
    if (!v) return;
    // Only values the roster can actually produce. An unknown one used to be
    // accepted, so a mistyped or last-season value added a chip that could
    // match nothing and the page read "0 of 636" -- blaming a filter for a
    // value that does not exist. Seeded values count as known even at zero
    // rows: a configured dish tag nothing matched is still a real choice.
    const known = knownFacetValues(f);
    v.split('~').forEach((x) => { if (known.has(x)) FILTERS[f.key].add(x); });
  });
  // Everything below arrives as user-editable text -- a forwarded link, a
  // truncated paste, a hand-edit -- so a value that cannot be read is IGNORED
  // rather than coerced, leaving the page as though the token were absent.
  // Coercing was never harmless: `by=garbage` compared lexically against every
  // end_date, filtered the whole roster out, announced that the season had
  // ended, and left the date control blank, because a type=date input silently
  // refuses a value it cannot parse -- so nothing on screen could clear it.
  const by = p.get('by');
  if (by === 'any') FILTERS.bookableBy = null;
  else if (isISODate(by)) FILTERS.bookableBy = by;
  // parseFloat('abc') is NaN, which is != null and so counted as an active
  // filter, printed a "Rating NaN+" chip, and compared false against every
  // score -- silently dropping every unrated restaurant.
  const mr = Number(p.get('minr'));
  FILTERS.minRating = p.get('minr') && Number.isFinite(mr) && mr >= 0 && mr <= 5
    ? mr : null;
  if (isISODate(p.get('to'))) FILTERS.endingBy = p.get('to');
  if (p.get('saved') === '1') FILTERS.savedOnly = true;
  if (p.get('q')) { QUERY = fold(p.get('q')); $('#q').value = p.get('q'); }
  // Object.hasOwn, not truthiness: "sort=constructor" inherits from
  // Object.prototype, would pass a truthy check and then be used as a comparator.
  const s = p.get('sort');
  if (s && Object.hasOwn(SORTS, s)) { SORT = s; $('#sort').value = SORT; }
}

/* ---------- overview ----------------------------------------------------- */

/* Charts are hand-built SVG — no library. Marks carry CSS CLASSES rather than
   inline fills, so a theme switch restyles them with no redraw.
   Every chart is also a control: clicking a mark applies the matching filter
   and drops you into the list. The overview always describes the WHOLE
   programme, never the filtered subset — otherwise drilling in would be
   circular. */

const NS = 'http://www.w3.org/2000/svg';
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS(NS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  return n;
};

function tip(host) {
  let box = host.querySelector('.tipBox');
  if (!box) {
    box = el('div', 'tipBox');
    box.hidden = true;
    host.append(box);
  }
  return {
    show(text, x, y) { box.textContent = text; box.style.left = `${x}px`; box.style.top = `${y}px`; box.hidden = false; },
    hide() { box.hidden = true; },
  };
}

/** Every bar is a filter control, and the stats note promises as much ("Click
 *  any bar to filter the list by it") — so each one has to be reachable and
 *  activatable without a mouse, and has to say what it is when it gets there.
 *  Focus goes on the invisible hit target, which is what the stylesheet's
 *  `.barHit:focus-visible + .bar` rule lights up. */
function barButton(node, label, activate) {
  node.setAttribute('role', 'button');
  node.setAttribute('tabindex', '0');
  node.setAttribute('aria-label', label);
  node.addEventListener('keydown', (e) => {
    // Space would scroll the page away from the chart unless it is claimed here
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); }
  });
}

/** Vertical bars — closings per date. Urgent dates take the status colour. */
function barsVertical(host, data, onPick, label) {
  host.textContent = '';
  const W = 560, H = 190, padL = 26, padR = 8, padT = 16, padB = 30;
  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': label });
  const max = Math.max(...data.map((d) => d.n), 1);
  const bw = (W - padL - padR) / data.length;
  const t = tip(host);

  svg.append(svgEl('line', { class: 'ax', x1: padL, y1: H - padB, x2: W - padR, y2: H - padB }));

  data.forEach((d, i) => {
    const h = Math.max(2, (H - padT - padB) * (d.n / max));
    const x = padL + i * bw + bw * 0.18;
    const w = bw * 0.64;
    const y = H - padB - h;

    const hit = svgEl('rect', { class: 'barHit', x: padL + i * bw, y: padT, width: bw, height: H - padT - padB });
    // 4px rounded data-end, anchored to the baseline
    const bar = svgEl('rect', { class: `bar${d.urgent ? ' urgent' : ''}`, x, y, width: w, height: h, rx: 4 });
    svg.append(hit, bar);
    if (h > 8) svg.append(svgEl('rect', { class: `bar${d.urgent ? ' urgent' : ''}`, x, y: y + h - 6, width: w, height: 6 }));

    const lbl = svgEl('text', { class: 'barLabel', x: x + w / 2, y: y - 5, 'text-anchor': 'middle' });
    lbl.textContent = d.n;
    svg.append(lbl);

    const ax = svgEl('text', { class: 'axLabel', x: x + w / 2, y: H - padB + 14, 'text-anchor': 'middle' });
    ax.textContent = d.label;
    svg.append(ax);

    const text = `${d.n} close ${d.label}${d.urgent ? ' · book first' : ''}`;
    const over = (e) => {
      const r = host.getBoundingClientRect();
      t.show(text, e.clientX - r.left, e.clientY - r.top);
    };
    [hit, bar].forEach((n) => {
      n.addEventListener('mousemove', over);
      n.addEventListener('mouseleave', () => t.hide());
      n.addEventListener('click', () => onPick(d));
      n.style.cursor = 'pointer';
    });
    barButton(hit, text, () => onPick(d));
    // The bar's reading is in the tooltip, so focus has to surface it too.
    hit.addEventListener('focus', () => {
      const r = host.getBoundingClientRect(), b = bar.getBoundingClientRect();
      t.show(text, b.left + b.width / 2 - r.left, b.top - r.top);
    });
    hit.addEventListener('blur', () => t.hide());
  });
  host.append(svg);
}

/** Horizontal bars — magnitude by category, single hue. */
function barsHorizontal(host, data, onPick, label) {
  host.textContent = '';
  const rowH = 26, padL = 116, padR = 44;
  const W = 560, H = data.length * rowH + 6;
  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': label });
  const max = Math.max(...data.map((d) => d.n), 1);
  const t = tip(host);

  data.forEach((d, i) => {
    const y = i * rowH + 4;
    const w = Math.max(3, (W - padL - padR) * (d.n / max));

    const hit = svgEl('rect', { class: 'barHit', x: 0, y, width: W, height: rowH - 4 });
    const bar = svgEl('rect', { class: 'bar', x: padL, y: y + 3, width: w, height: rowH - 12, rx: 4 });
    // square off the baseline end so only the data-end is rounded
    const cap = svgEl('rect', { class: 'bar', x: padL, y: y + 3, width: Math.min(5, w), height: rowH - 12 });
    svg.append(hit, bar, cap);

    const name = svgEl('text', { class: 'axLabel', x: padL - 8, y: y + rowH / 2, 'text-anchor': 'end', 'dominant-baseline': 'middle' });
    name.textContent = d.label;
    const val = svgEl('text', { class: 'barLabel', x: padL + w + 7, y: y + rowH / 2, 'dominant-baseline': 'middle' });
    val.textContent = d.n;
    svg.append(name, val);

    const text = `${d.label} · ${d.n}`;
    const over = (e) => {
      const r = host.getBoundingClientRect();
      t.show(text, e.clientX - r.left, e.clientY - r.top);
    };
    [hit, bar].forEach((n) => {
      n.addEventListener('mousemove', over);
      n.addEventListener('mouseleave', () => t.hide());
      n.addEventListener('click', () => onPick(d));
      n.style.cursor = 'pointer';
    });
    barButton(hit, text, () => onPick(d));
    hit.addEventListener('focus', () => {
      const r = host.getBoundingClientRect(), b = bar.getBoundingClientRect();
      t.show(text, b.left + b.width / 2 - r.left, b.top - r.top);
    });
    hit.addEventListener('blur', () => t.hide());
  });
  host.append(svg);
}

/** One stacked bar — part-to-whole, with direct labels so the segments never
 *  depend on colour alone (verified green vs estimate amber are close under
 *  protanopia). 2px surface gaps separate the fills. */
function stackedBar(host, segs, onPick, label) {
  host.textContent = '';
  const W = 560, H = 46, GAP = 2;
  const total = segs.reduce((a, s) => a + s.n, 0) || 1;
  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': label });
  let x = 0;
  segs.forEach((s, i) => {
    const w = Math.max(2, (W * s.n) / total - (i < segs.length - 1 ? GAP : 0));
    const r = svgEl('rect', {
      class: `seg ${s.key}`, x, y: 0, width: w, height: 26,
      rx: i === 0 || i === segs.length - 1 ? 4 : 0,
    });
    r.addEventListener('click', () => onPick(s));
    // No hit rect here — the segment is its own target, so it carries focus.
    barButton(r, `${s.n} ${s.label} · ${Math.round((s.n / total) * 100)}%`, () => onPick(s));
    svg.append(r);

    if (w > 42) {
      const pct = svgEl('text', { class: 'barLabel', x: x + w / 2, y: 43, 'text-anchor': 'middle' });
      pct.textContent = `${Math.round((s.n / total) * 100)}%`;
      svg.append(pct);
    }
    x += w + GAP;
  });
  host.append(svg);

  const leg = el('div', 'chartLegend');
  segs.forEach((s) => {
    const item = el('span');
    item.append(Object.assign(el('i', s.key), {}));
    item.append(el('b', null, String(s.n)));
    item.append(document.createTextNode(` ${s.label}`));
    leg.append(item);
  });
  host.append(leg);
}

function renderStats() {
  if (!DATA) return;
  // Every tile and every chart here is a statement about the PROGRAMME — how
  // many are in it, when they close, what the value figures rest on. Your own
  // places are not in it, so counting them would make each of those false.
  const all = ROWS.filter((r) => !isMine(r));
  const today = todayISO();
  /* INCLUSIVE of today, because every other date test on this page is: a
     restaurant ending today has not ended (hasEnded), still counts as urgent
     (isUrgent), and the season is not archived until we are PAST program_end
     (seasonPhase). Counting exclusively made the last day of the season read
     "0 days left" beside "401 close by Sep 6" — a day you can still book and
     eat, announced as over.

     No hardcoded fallback date. When a payload carries no program_end -- a
     browser holding a cached one from before the field existed -- the honest
     answer is that we do not know, not a countdown to a date from 2026. */
  const daysLeft = DATA.program_end ? Math.max(0, 1 + Math.round(
    (Date.parse(`${DATA.program_end}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 864e5)) : null;
  // Past the end, both date tiles become facts about a finished season rather
  // than a countdown: "0 days left" and "0 close by Sep 6" are each false.
  const over = seasonPhase() === 'archive';
  const soon = all.filter(over
    ? (r) => r.end_date && r.end_date <= urgencyHorizon()
    : isUrgent).length;
  const stars = all.filter((r) => (r.recognition || [])
    .some((x) => x.source === 'michelin' && /star/.test(x.level || ''))).length;

  const tiles = [
    { n: all.length, k: 'restaurants' },
    over ? { n: fmtDate(DATA.program_end), k: 'season ended' }
         : { n: daysLeft == null ? '—' : daysLeft,
             k: daysLeft === 1 ? 'day left' : 'days left' },
    { n: soon, k: `${over ? 'ended' : 'close'} by ${fmtDate(urgencyHorizon())}`,
      cls: !over && soon ? 'crit' : null },
    { n: all.filter((r) => r.gap_basis === 'verified').length, k: 'verified gaps', cls: 'value' },
    { n: stars, k: 'michelin stars' },
    { n: SAVED.size, k: 'you saved' },
  ];
  // Say so, rather than leaving the reader to notice that the count here and
  // the count in the status bar disagree by however many places you added.
  const note = $opt('#stats .statsNote');
  if (ROWS.length > all.length) {
    note.textContent = 'The whole programme — the filters above don’t apply here, '
      + 'and your own places are left out of every figure. Click any bar to filter the list by it.';
  }

  const th = $('#tiles');
  th.textContent = '';
  tiles.forEach((t) => {
    const d = el('div', `tile${t.cls ? ' ' + t.cls : ''}`);
    d.append(el('span', 'n', String(t.n)));
    d.append(el('span', 'k', t.k));
    th.append(d);
  });

  const jump = (fn) => { clearAll(true); fn(); VIEW = 'list'; apply(); setView('list'); };

  // closings by date
  const byDate = {};
  all.forEach((r) => { if (r.end_date) byDate[r.end_date] = (byDate[r.end_date] || 0) + 1; });
  const dates = Object.keys(byDate).sort().map((d) => ({
    key: d, label: fmtDate(d), n: byDate[d], urgent: d >= todayISO() && d <= urgencyHorizon(),
  }));
  // A chart is a picture of a number, so its accessible name has to carry the
  // number — read aloud, "When restaurants stop serving" alone says nothing.
  const withEnd = dates.reduce((a, d) => a + d.n, 0);
  const peak = dates.reduce((a, d) => (d.n > a.n ? d : a), dates[0]);
  barsVertical($('#chartClose .plot'), dates, (d) => jump(() => {
    // both bounds on the same day == "closes exactly then"
    FILTERS.bookableBy = d.key; FILTERS.endingBy = d.key; SORT = 'gap_usd_desc';
  }), peak
    ? `When restaurants stop serving: ${peak.n} of ${withEnd} close ${peak.label}, `
      + `over ${dates.length} closing date${dates.length === 1 ? '' : 's'}`
    : 'When restaurants stop serving: no end dates in this season');

  // value basis
  const basis = [
    { key: 'verified', label: 'verified', n: all.filter((r) => r.gap_basis === 'verified').length },
    { key: 'estimate', label: 'estimated', n: all.filter((r) => r.gap_basis === 'estimate').length },
    { key: 'none', label: 'no comparable', n: all.filter((r) => !r.gap_basis).length },
  ];
  stackedBar($('#chartBasis .plot'), basis, (s) => jump(() => FILTERS.basis.add(s.key)),
    `What the value figures rest on: of ${all.length}, `
    + basis.map((s) => `${s.n} ${s.label}`).join(', '));
  // The caption's ratio is the headline of this chart and moves with the data —
  // hand-verifying one more restaurant must not leave a stale figure printed.
  const verified = basis[0].n;
  $opt('#basisRatio').textContent = verified
    ? `only 1 in ${Math.round(all.length / verified)} is hand-verified`
    : 'none is hand-verified yet';

  const listOf = (d) => d.map((x) => `${x.label} ${x.n}`).join(', ');

  // borough
  const byB = {};
  all.forEach((r) => { if (r.borough) byB[r.borough] = (byB[r.borough] || 0) + 1; });
  const boroughs = Object.entries(byB).sort((a, b) => b[1] - a[1]).map(([k, n]) => ({ key: k, label: k, n }));
  barsHorizontal($('#chartBorough .plot'), boroughs,
    (d) => jump(() => FILTERS.borough.add(d.key)),
    `Where they are: of ${all.length}, ${listOf(boroughs)}`);

  // cuisines
  const byC = {};
  all.forEach((r) => (r.cuisines || []).forEach((c) => { byC[c] = (byC[c] || 0) + 1; }));
  const cuisines = Object.entries(byC).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([k, n]) => ({ key: k, label: k, n }));
  // Restaurants carry more than one cuisine, so these do NOT sum to the total —
  // the name says "of N cuisines", never "of N restaurants".
  barsHorizontal($('#chartCuisine .plot'), cuisines,
    (d) => jump(() => FILTERS.cuisine.add(d.key)),
    `Most common cuisines: the top ${cuisines.length} of ${Object.keys(byC).length} — ${listOf(cuisines)}`);
}

/* ---------- plan --------------------------------------------------------- */

/* Turns the shortlist into dated bookings. The point is that the app knows
   which dates are actually possible: inside the restaurant's window, and on a
   weekday it genuinely serves. Restaurant Week excludes Saturdays except where
   a restaurant prints otherwise, and a Sunday is only offered where Sunday
   service is established. */

/* PLAN and persistPlan live with SAVED at the top of the file — both are
   per-season state and both are swapped by loadState(). */

/* How far ahead your own places can be dated. They have no window to bound
   them, so the list has to be bounded by something; eight weeks is long
   enough to plan against and short enough to still be a list. */
const MINE_HORIZON_DAYS = 56;

/** Why a given date is or isn't bookable — the reason is shown, not just the
 *  verdict, because several of these come from contested source data. */
function dateIssue(r, iso) {
  if (iso < todayISO()) return 'in the past';
  // The window, the Saturday exclusion and the Sunday rule are all rules OF
  // the programme. Your own places are not in it, so none of them binds here.
  if (isMine(r)) return null;
  if (r.end_date && iso > r.end_date) return `after it closes (${fmtDate(r.end_date)})`;
  const dow = dowOf(iso);
  if (dow === 6 && !(r.flags || []).includes('saturday_service')) {
    return 'Saturdays are excluded from the programme';
  }
  if (dow === 0) {
    if (r.sunday !== true) return 'no Sunday service';
    /* The listing distinguishes "$N Sunday Dinner Price" from "$N Sunday
       Lunch/Brunch Price", so it says this for every restaurant. This branch
       used to fire only on a hand-set `no_sunday_dinner` flag, which exactly
       one restaurant carries — and that one's `sunday` is false, so the line
       above returns first and the branch had never run for anybody. The other
       23 brunch-only restaurants were offered a Sunday with no qualification.
       The flag still wins where it is set; `sunday_dinner` folds it in. */
    if (r.sunday_dinner === false) return 'Sunday brunch only, no dinner';
  }
  return null;
}

function validDates(r) {
  const out = [];
  /* Without a program_end there is no programme window to plan inside, so the
     restaurant's own end date decides and, failing that, the same horizon your
     own places get. Falling back to a hardcoded 2026-09-06 meant that in any
     later season the loop below started after `last` and silently produced NO
     dates at all -- a planner that offers nothing, with nothing saying why. */
  const end = DATA.program_end
    || r.end_date || addDays(todayISO(), MINE_HORIZON_DAYS);
  const last = isMine(r) ? addDays(todayISO(), MINE_HORIZON_DAYS)
    : r.end_date && r.end_date < end ? r.end_date : end;
  for (let d = todayISO(); d <= last; d = addDays(d, 1)) {
    if (!dateIssue(r, d)) out.push(d);
  }
  return out;
}

function renderPlan() {
  const host = $('#planBody');
  host.textContent = '';
  const bySlug = new Map(ROWS.map((r) => [r.slug, r]));
  const saved = [...SAVED].map((s) => bySlug.get(s)).filter(Boolean);

  if (!saved.length) {
    const e = el('div', 'empty');
    e.append(el('p', null, 'Nothing saved yet.'));
    e.append(el('p', null, 'Open a restaurant and press ★ Save, then come back here to give it a date.'));
    host.append(e);
    return;
  }

  /* Assignments are never dropped here. A date that has passed is a meal you
     went to, not stale state, and deleting it on render silently destroyed the
     only record of it. Impossible-but-future dates keep their reason instead. */

  const dated = saved.filter((r) => PLAN.get(r.slug))
    .sort((a, b) => PLAN.get(a.slug).localeCompare(PLAN.get(b.slug)));
  const undated = saved.filter((r) => !PLAN.get(r.slug))
    .sort((a, b) => (a.end_date || 'z').localeCompare(b.end_date || 'z'));

  const section = (title, list, note) => {
    if (!list.length) return;
    const s = el('section', 'planSec');
    const h = el('h2', null, title);
    h.append(el('span', 'n', ` ${list.length}`));
    s.append(h);
    if (note) s.append(el('p', 'planNote', note));

    // conflict = two bookings on one date
    const counts = {};
    list.forEach((r) => { const d = PLAN.get(r.slug); if (d) counts[d] = (counts[d] || 0) + 1; });

    list.forEach((r) => {
      const row = el('div', 'planRow');
      const when = PLAN.get(r.slug);

      const main = el('div', 'planMain');
      const nm = el('div', 'planName');
      if (r.rank != null) nm.append(el('span', 'rankBadge', `#${r.rank}`));
      nm.append(el('span', 'rname', r.name));
      // Says why this one has no closing date and a much longer date list.
      if (isMine(r)) nm.append(pill('quiet', 'my list', 'Not a Restaurant Week participant'));
      main.append(nm);

      const meta = [r.neighborhood, (r.price_tiers || []).join('/')].filter(Boolean).join(' · ');
      const sub = el('div', 'planMeta', meta);
      if (r.end_date) {
        sub.append(el('span', 'sep', ' · '));
        sub.append(el('span', null, `closes ${fmtDate(r.end_date)}`));
      }
      const lex = ['4', '5', '6'].map((k) => r.subway && r.subway[k]).filter((v) => v != null);
      if (lex.length) {
        sub.append(el('span', 'sep', ' · '));
        sub.append(el('span', 'mono', `~${Math.min(...lex)} min to 4/5/6`));
      }
      main.append(sub);

      if (when && counts[when] > 1) {
        main.append(el('div', 'planWarn', `Two bookings on ${fmtDate(when)}`));
      }
      // A booked date that has passed reads as history; anything else that has
      // become impossible says WHY, which is what dateIssue() was written for.
      const issue = when && dateIssue(r, when);
      if (issue === 'in the past') {
        main.append(el('div', 'planPast', `went here — ${fmtDate(when)}`));
      } else if (issue) {
        main.append(el('div', 'planWarn', `${fmtDate(when)} is ${issue}`));
      }
      row.append(main);

      const pick = el('div', 'planPick');
      const sel = el('select', 'planSel');
      sel.dataset.slug = r.slug;
      sel.setAttribute('aria-label', `Date for ${r.name}`);
      const none = el('option', null, when ? 'Unschedule' : 'Pick a date…');
      none.value = '';
      sel.append(none);
      const options = validDates(r);
      // An assignment outside the offerable set still has to appear, or the
      // control would misreport what is actually booked.
      const shown = when && !options.includes(when) ? [when, ...options] : options;
      shown.forEach((d) => {
        const o = el('option', null, `${DAYS[dowOf(d)]} ${fmtDate(d)}`);
        o.value = d;
        if (d === when) o.selected = true;
        sel.append(o);
      });
      if (!shown.length) {
        sel.disabled = true;
        none.textContent = seasonPhase() === 'archive' ? 'Season over' : 'No dates left';
      }
      sel.addEventListener('change', () => {
        if (sel.value) PLAN.set(r.slug, sel.value); else PLAN.delete(r.slug);
        persistPlan();
        renderPlan();
        // Dating a restaurant moves it between sections, and the rebuild throws
        // away the very control you were using. Put focus back on its
        // replacement, or every pick drops you to the top of the document.
        const again = host.querySelector(`.planSel[data-slug="${CSS.escape(r.slug)}"]`);
        if (again) again.focus();
      });
      pick.append(sel);
      if (options.length) {
        pick.append(el('span', 'planCount', `${options.length} possible`));
      }
      row.append(pick);

      if (r.links && isHttpURL(r.links.reservation)) {
        const a = el('a', 'linkBtn primary', 'Book');
        a.href = r.links.reservation; a.target = '_blank'; a.rel = 'noopener noreferrer';
        row.append(a);
      }
      s.append(row);
    });
    host.append(s);
  };

  section('Scheduled', dated);
  // The window/Saturday/Sunday rules are the programme's, so claiming them over
  // a list that is all my own places would describe a constraint that isn't there.
  const undatedNote = seasonPhase() === 'archive'
    ? 'The season is over, so these are what never got a date. Kept as part of the record.'
    : undated.every(isMine)
      ? 'Your own places carry no programme window, so any day from today is offered.'
      : 'The date list for each is limited to days it is actually open — inside its window, and not a Saturday or a Sunday it does not serve.';
  section('Not yet scheduled', undated, undatedNote);
}

/* ---------- compare ------------------------------------------------------ */

/* Attributes down the side, restaurants across. The last decision is "which of
   my saved four?", and that is a row-by-row comparison, not four separate
   detail panels you have to hold in your head. `best` marks the winning cell
   where a row HAS a winner -- and deliberately does not where it doesn't.
   `rw: true` marks a row the programme owns: it is blank, and never a winner,
   for a place on your own list. Several of these print a word ("No
   comparable", "not stated", "None") when the value is null, and on a row
   that was never in the programme each of those reads as a checked fact. */
const CMP_ROWS = [
  // First row: the composite, with the share of it that rested on real data.
  // Shown together on purpose — a 78 built on everything and a 71 built on
  // three quarters are not the same claim, and the winner dot would be
  // misleading without it.
  { k: 'Rubric grade', mono: true,
    get: (r) => (r.rubric == null ? '—'
      : `${r.rubric.toFixed(1)}${r.rubric_completeness < 100
          ? ` · ${Math.round(r.rubric_completeness)}% known` : ''}`),
    raw: (r) => r.rubric, bestRaw: 'max' },
  { k: 'Rank', rw: true, get: (r) => (r.rank != null ? `#${r.rank}` : '—'), mono: true,
    best: (v, all) => v !== '—' && v === all.filter((x) => x !== '—')
      .sort((a, b) => +a.slice(1) - +b.slice(1))[0] },
  { k: 'Gap', mono: true, rw: true,
    get: (r) => (r.gap_usd == null ? '—'
      : `${r.gap_usd < 0 ? '+' : ''}${money(Math.abs(r.gap_usd))}`
        + (r.gap_pct != null ? ` · ${Math.abs(r.gap_pct)}%` : '')),
    raw: (r) => (r.gap_usd == null ? null : r.gap_usd),
    bestRaw: 'max' },
  { k: 'Basis', rw: true, get: (r) => (r.gap_basis === 'verified' ? 'Verified'
    : r.gap_basis === 'estimate' ? 'Estimate' : 'No comparable'),
    best: (v) => v === 'Verified' },
  { k: 'RW price', rw: true, get: (r) => (r.rw_price != null ? money(r.rw_price) : '—'), mono: true,
    raw: (r) => r.rw_price, bestRaw: 'min' },
  { k: 'Comparable', rw: true, get: (r) => (r.comparable_usd != null ? money(r.comparable_usd) : '—'), mono: true },
  { k: 'Window ends', mono: true, rw: true,
    get: (r) => (r.end_date ? `${fmtDate(r.end_date)}${r.end_date_source === 'printed' ? '' : ' ?'}` : 'not stated'),
    raw: (r) => (r.end_date ? Date.parse(r.end_date) : null), bestRaw: 'max' },
  { k: 'Days', rw: true, get: (r) => r.days || '—' },
  { k: 'Sunday', rw: true, get: (r) => (r.sunday === true
    ? (r.sunday_source === 'verified' ? 'Yes — verified' : 'Yes — unverified')
    : r.sunday === false
      ? (r.sunday_source === 'verified' ? 'No — verified' : 'No — unverified')
      : 'Not established'),
    best: (v) => v === 'Yes — verified' },
  { k: 'Recognition', get: (r) => (r.recognition || [])
    .map((b) => `${b.source_label} ${b.level}`).slice(0, 2).join(', ') || '—' },
  // Your own places carry neither, and an empty cell reads as a rendering
  // fault where the dash next to it reads as "not known".
  { k: 'Where', get: (r) => [r.neighborhood, r.borough].filter(Boolean).join(', ') || '—' },
  { k: 'Nearest subway', mono: true,
    get: (r) => (r.subway_nearest
      ? `${r.subway_nearest.name} ${r.subway_nearest.min}m · ${(r.subway_nearest.routes || []).join('')}`
      : '—'),
    raw: (r) => (r.subway_nearest ? r.subway_nearest.min : null), bestRaw: 'min' },
  { k: 'Outdoor', rw: true, get: (r) => {
      const o = r.outdoor;
      if (!o) return 'Not established';
      if (!o.licensed) return 'Says so on its listing';
      return [o.sidewalk && 'Sidewalk', o.roadway && 'Street shed']
        .filter(Boolean).join(' + ');
    },
    best: (v) => v !== 'Not established' && v !== 'Says so on its listing' },
  { k: 'Menu', rw: true, get: (r) => (r.menu_state === 'pdf' ? 'PDF published'
    : r.menu_state === 'image_only' ? 'Image-only PDF' : 'None') },
];

const CMP_MAX = 6;   // more columns than this and the table stops being readable

function renderCompare() {
  const saved = ROWS.filter((r) => SAVED.has(r.slug))
    .sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99) || a._name.localeCompare(b._name));
  const picks = saved.slice(0, CMP_MAX);

  // Saying "your 6 saved restaurants" over a capped slice quietly loses the
  // 7th: name both numbers, and which ones these are.
  const lead = saved.length > picks.length
    ? `The top ${picks.length} of your ${saved.length} saved restaurants, by rank, side by side. `
    : `Your ${picks.length} saved restaurant${picks.length === 1 ? '' : 's'}, side by side. `;
  $('#cmpNote').textContent = picks.length
    ? lead
      + 'A dot marks the better value in rows where there is one. '
      + '"?" on a date means the restaurant does not print it.'
    : 'Nothing saved yet — open a restaurant and press ★ Save to compare it here.';

  const t = $('#cmpTable');
  t.textContent = '';
  if (!picks.length) return;

  const head = el('tr');
  head.append(el('th', 'cmpCorner', ''));
  picks.forEach((r) => {
    const th = el('th');
    const a = el('a', 'cmpName', r.name);
    a.href = `#r=${encodeURIComponent(r.slug)}`;
    th.append(a);
    const rm = el('button', 'cmpRemove', '×');
    rm.type = 'button';
    rm.title = `Remove ${r.name}`;
    rm.addEventListener('click', () => {
      SAVED.delete(r.slug); persistSaved();
      buildPresets(); renderCompare(); syncCompareBtn();
    });
    th.append(rm);
    head.append(th);
  });
  t.append(head);

  CMP_ROWS.forEach((row) => {
    const tr = el('tr');
    tr.append(el('th', 'cmpKey', row.k));
    const na = (r) => row.rw && isMine(r);
    const vals = picks.map((r) => (na(r) ? '—' : row.get(r)));
    let winners = new Set();
    if (row.bestRaw) {
      const nums = picks.filter((r) => !na(r)).map(row.raw).filter((v) => v != null);
      if (nums.length > 1) {
        const target = row.bestRaw === 'max' ? Math.max(...nums) : Math.min(...nums);
        // no dot when everything ties — a dot on every cell says nothing
        if (new Set(nums).size > 1) {
          picks.forEach((r, i) => { if (!na(r) && row.raw(r) === target) winners.add(i); });
        }
      }
    }
    if (row.best) vals.forEach((v, i) => { if (!na(picks[i]) && row.best(v, vals)) winners.add(i); });
    // Marking every cell in a row marks nothing — if they all tie, drop it.
    if (winners.size === picks.length) winners = new Set();

    vals.forEach((v, i) => {
      const td = el('td', row.mono ? 'mono' : null);
      if (winners.has(i)) td.classList.add('cmpBest');
      td.textContent = v;
      tr.append(td);
    });
    t.append(tr);
  });

  const tr = el('tr');
  tr.append(el('th', 'cmpKey', 'Book'));
  picks.forEach((r) => {
    const td = el('td', 'cmpBook');
    const L = r.links || {};
    if (isHttpURL(L.reservation)) {
      // Name the platform: you should know whether you are about to land on
      // OpenTable or a bespoke widget before you click.
      const a = el('a', 'linkBtn primary', 'Book');
      a.href = L.reservation; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.title = `Book ${r.name} — ${bookingHost(L.reservation)}`;
      td.append(a);
      td.append(el('span', 'cmpVia', bookingHost(L.reservation)));
    } else if (isHttpURL(L.website)) {
      // 256 of 645 have no booking link at all. Sending you to the restaurant's
      // own site is still useful, but it is NOT a reservation link and must not
      // be dressed as one — hence the quieter button and the honest label.
      const a = el('a', 'linkBtn', 'Restaurant site');
      a.href = L.website; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.title = `${r.name} publishes no booking link — try their own site or call`;
      td.append(a);
      td.append(el('span', 'cmpVia', 'no booking link'));
    } else {
      td.textContent = '—';
    }
    tr.append(td);
  });
  t.append(tr);
}

/** Which platform a reservation url lands on, for the label. */
function bookingHost(url) {
  const m = /^https?:\/\/(?:www\.)?([^/]+)/i.exec(url || '');
  const h = (m ? m[1] : '').toLowerCase();
  if (h.includes('opentable')) return 'OpenTable';
  if (h.includes('resy')) return 'Resy';
  if (h.includes('sevenrooms')) return 'SevenRooms';
  if (h.includes('tocktix') || h.includes('exploretock')) return 'Tock';
  return h.replace(/^m\./, '') || 'link';
}

/** The Compare tab only exists once there is something to compare. */
function syncCompareBtn() {
  const b = document.querySelector('.segBtn[data-view="compare"]');
  if (!b) return;
  b.hidden = SAVED.size < 2;
  if (b.hidden && VIEW === 'compare') setView('list');
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

/* A custom-property read resolves the document's style, and asking per marker
   made that 630 reads per redraw — on every apply() while the map is open.
   The four colours cannot change mid-redraw, so a redraw reads them once; a
   theme change repaints, which reads them again. */
const markerPalette = () => {
  const cs = getComputedStyle(document.documentElement);
  const v = (name) => cs.getPropertyValue(name).trim();
  return { crit: v('--crit'), value: v('--value'), warn: v('--warn'), none: v('--ink-3') };
};

const markerColour = (r, pal) =>
  isUrgent(r) ? pal.crit
    : r.gap_basis === 'verified' ? pal.value
    : r.gap_basis === 'estimate' ? pal.warn
    : pal.none;

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
  // Same three states the row pill uses: a marker that is red for urgency must
  // not describe itself as merely running through a date.
  box.append(el('div', 'meta',
    isMine(r) ? 'On your own list — not a Restaurant Week participant'
      : !r.end_date ? 'No end date published'
        : hasEnded(r) ? `Ended ${fmtDate(r.end_date)}`
          : isUrgent(r) ? `Book by ${fmtDate(r.end_date)}`
            : `Runs through ${fmtDate(r.end_date)}`));

  const acts = el('div', 'acts');
  if (r.links && isHttpURL(r.links.reservation)) {
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
  // No marker can be red in the archive, and a key to a colour that isn't
  // there reads as "none of these are closing soon" rather than "none can be".
  $('#legendUrgent').hidden = seasonPhase() === 'archive';
  if (MAP_LAYER) MAP_LAYER.remove();
  MAP_LAYER = L.layerGroup().addTo(MAP);

  const pal = markerPalette();
  const pts = RESULTS.filter((r) => r.lat != null && r.lng != null);
  pts.forEach((r) => {
    const c = markerColour(r, pal);
    // Verified green and estimate amber are ΔE 4.7 apart under protanopia —
    // indistinguishable by colour alone. The list encodes basis in FORM too
    // (dashed underline + "estimate"), so the map does the same: a solid disc
    // is verified, a hollow ring is an estimate, a small faint dot is no
    // comparable. Readable in greyscale and with any colour vision.
    const solid = r.gap_basis === 'verified';
    const hollow = r.gap_basis === 'estimate';
    L.circleMarker([r.lat, r.lng], {
      radius: r.rank != null ? 8 : solid ? 6 : hollow ? 6 : 4,
      color: c,
      weight: r.rank != null ? 3 : hollow ? 2.5 : 1.5,
      fillColor: c,
      fillOpacity: hollow ? 0.05 : SAVED.has(r.slug) ? 0.95 : solid ? 0.75 : 0.35,
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
  const onStats = v === 'stats';
  const onPlan = v === 'plan';
  const onCmp = v === 'compare';
  $('#mapWrap').hidden = !onMap;
  $('#stats').hidden = !onStats;
  $('#plan').hidden = !onPlan;
  $('#compare').hidden = !onCmp;
  $('#rows').hidden = onMap || onStats || onPlan || onCmp;
  $('#showMore').hidden = onMap || onStats || onPlan || onCmp || RENDERED >= RESULTS.length;
  document.querySelectorAll('.segBtn').forEach((b) =>
    b.setAttribute('aria-pressed', b.dataset.view === v ? 'true' : 'false'));
  BANNER();
  if (onMap) openMap();
  if (onStats) renderStats();
  if (onPlan) renderPlan();
  if (onCmp) renderCompare();
}

/* ---------- theme ------------------------------------------------------- */

function initTheme() {
  const saved = recall('rw-theme');
  if (saved) document.documentElement.dataset.theme = saved;
  $('#themeToggle').addEventListener('click', () => {
    const cur = document.documentElement.dataset.theme
      || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    store('rw-theme', next);
    if (MAP) { paintTiles(); drawMarkers(); }   // tiles + marker colours are themed
  });
}

/* ---------- seasons ------------------------------------------------------ */

async function fetchJSON(url) {
  const res = await fetch(url, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** The registry, or [] when there isn't one — which is what a site published
 *  before the exporter wrote seasons.json looks like, and it must still boot
 *  off the legacy single-season payload. */
async function loadRegistry() {
  try {
    const reg = await fetchJSON(REGISTRY_URL);
    return (reg.seasons || []).filter((s) => s && s.code && s.file);
  } catch { return []; }
}

/** The requested code if the registry has it, else the live one, else the
 *  newest by end date. `status` is stamped by each season's own last build and
 *  goes stale, so it is a hint here and the dates are the fallback. */
function chooseSeason(list, code) {
  return (code && list.find((s) => s.code === code))
    || list.find((s) => s.status === 'live')
    || [...list].sort((a, b) => String(b.end || '').localeCompare(String(a.end || '')))[0];
}

const seasonURL = (s) => DATA_DIR + String(s.file).replace(/^\/+/, '');

/** Your own places. Not having any is the normal case, not a failure. */
async function loadPlaces() {
  try {
    const p = await fetchJSON(PLACES_URL);
    return (p.places || []).map(prepare);
  } catch { return []; }
}

/** Everything on the page that names the season: the masthead control, the
 *  provenance line and the archive banner. Re-run after every switch — with a
 *  switcher the phase really can change under you, so unlike before, none of
 *  this can be decided once at boot. */
function renderSeasonChrome() {
  const host = $opt('#seasonLabel');
  if (SEASONS.length > 1) {
    if (host.tagName === 'SELECT') host.value = SEASON.code;
    else if (host.parentNode) host.replaceWith(seasonSelect());
  } else if (host.parentNode) {
    host.textContent = DATA.season_label || '';
  }

  $opt('#footProvenance').textContent =
    `${(DATA.restaurants || []).length} participants · listing snapshot ${DATA.snapshot_date || '—'} · ` +
    `verified facts hand-checked ${DATA.verified_asof || '—'} · built ${(DATA.generated_at || '').slice(0, 10)}`;

  const over = $opt('#seasonOver');
  over.textContent = '';
  over.hidden = true;
  if (seasonPhase() !== 'archive') return;
  const p = el('p');
  p.append(el('strong', null, `${DATA.season_label || 'The season'} has ended.`));
  p.append(document.createTextNode(DATA.season_start
    ? ` The programme ran ${fmtDate(DATA.season_start)} – ${fmtDate(DATA.program_end)}.`
    : ` The last windows closed ${fmtDate(DATA.program_end)}.`));
  p.append(document.createTextNode(
    ' This is the full-season archive: every participant is listed, none is still bookable.'));
  over.append(p);
  over.hidden = false;
}

function seasonSelect() {
  const sel = el('select', 'season seasonSel');
  sel.id = 'seasonLabel';
  sel.setAttribute('aria-label', 'Season');
  SEASONS.forEach((s) => {
    const o = el('option', null, s.label || s.code);
    o.value = s.code;
    if (SEASON && s.code === SEASON.code) o.selected = true;
    sel.append(o);
  });
  sel.addEventListener('change', () => switchSeason(sel.value));
  return sel;
}

/** Swap the page over to another season: its payload, its shortlist, its plan,
 *  its phase. Filters are dropped rather than carried across — a neighborhood
 *  would survive the move but "closes by Aug 21" is meaningless in it. */
let SWITCH_SEQ = 0;   // two picks in flight: only the last one may land
async function switchSeason(code) {
  const entry = SEASONS.find((s) => s.code === code);
  if (!entry || (SEASON && entry.code === SEASON.code)) return;
  const seq = ++SWITCH_SEQ;
  let payload;
  try {
    payload = await fetchJSON(seasonURL(entry));
  } catch (err) {
    if (seq !== SWITCH_SEQ) return;
    // Nothing has changed yet, so put the control back on the season that is
    // actually on screen rather than letting it name one that failed to load.
    renderSeasonChrome();
    $('#rows').textContent = '';
    $('#rows').append(Object.assign(el('div', 'empty'), { textContent:
      `Could not load ${entry.label || entry.code} — ${err.message}. `
      + `Still showing ${DATA.season_label || 'the season already open'}.` }));
    return;
  }
  if (seq !== SWITCH_SEQ) return;   // a later pick already won
  SEASON = entry;
  DATA = payload;
  loadState(entry.code);          // the shortlist and the plan are per season
  EXPANDED.clear();               // slugs belong to the season they came from
  ROWS = (DATA.restaurants || []).map(prepare).concat(PLACES);
  renderSeasonChrome();
  clearAll(true);
  // Re-decided, not carried: the default is a fact about the season, and boot
  // makes the same call for whichever season it opens on.
  if (seasonPhase() !== 'archive') FILTERS.bookableBy = todayISO();
  apply();
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
    (r.recognition || []).map((x) => `${RECOG_LABEL[x.source] || x.source} ${x.tier || ''} ${x.level || ''} ${x.matched_name || ''}`).join(' '),
    r.verdict, r.verdict_note, r.days,
    // so "outdoor" / "patio" / "sidewalk" reach the rows that have it
    r.outdoor ? `outdoor alfresco ${r.outdoor.sidewalk ? 'sidewalk patio cafe' : ''} `
              + `${r.outdoor.roadway ? 'roadway street shed' : ''}` : '',
  ].filter(Boolean).join(' • '));
  return r;
}

async function boot() {
  initTheme();
  migrateLegacyState();

  // A megabyte of season comes down before there is a single row to draw, and
  // until it lands the page is a masthead over nothing — which reads as broken
  // rather than as busy. The first apply() clears this by emptying #rows; the
  // failure path below has to clear it itself.
  $('#rows').append(el('div', 'empty', 'Loading the season…'));

  SEASONS = await loadRegistry();
  SEASON = SEASONS.length
    ? chooseSeason(SEASONS, new URLSearchParams(location.hash.replace(/^#/, '')).get('season'))
    : null;
  const url = SEASON ? seasonURL(SEASON) : LEGACY_URL;
  try {
    DATA = await fetchJSON(url);
  } catch (err) {
    $('#rows').textContent = '';
    $('#rows').append(Object.assign(el('div', 'empty'), { textContent:
      `Could not load ${url} — ${err.message}. Run: python src/export_site_data.py` }));
    return;
  }

  loadState(SEASON ? SEASON.code : LEGACY_SEASON);
  // Season-agnostic and fetched once: your list is yours whatever season is on
  // screen, and a missing places.json is the ordinary case, not an error.
  PLACES = await loadPlaces();
  ROWS = (DATA.restaurants || []).map(prepare).concat(PLACES);

  renderSeasonChrome();

  // Default view: everything still bookable today, biggest gap first. In the
  // archive that date matches nothing, so the season opens whole rather than
  // as "0 of 636" blamed on filters nobody set.
  if (seasonPhase() !== 'archive') FILTERS.bookableBy = todayISO();
  readHash();

  $('#q').addEventListener('input', queueQuery);
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
  const isDismissed = () => recall('rw-banner') === 'off';
  $('#dismissBanner').addEventListener('click', () => {
    store('rw-banner', 'off');
    banner.hidden = true;
  });
  $('#verifiedOnly').addEventListener('click', () => {
    FILTERS.basis.clear();
    FILTERS.basis.add('verified');
    apply();
  });
  BANNER = () => {
    banner.hidden = isDismissed()
      || VIEW !== 'list'
      || SORT !== 'gap_usd_desc'   // only the raw-gap sort leads with estimates
      || FILTERS.basis.size > 0;
  };

  const fb = $('#filterBtn');
  fb.addEventListener('click', () => {
    const open = fb.getAttribute('aria-expanded') === 'true';
    fb.setAttribute('aria-expanded', open ? 'false' : 'true');
    $('#panel').hidden = open;
  });

  $('#viewSeg').addEventListener('click', (e) => {
    const b = e.target.closest('.segBtn');
    if (!b) return;
    setView(b.dataset.view);
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
    // null whenever the document itself is not focused — reading .tagName off
    // it throws, and the throw kills every other key on the page.
    const focused = document.activeElement;
    const typing = !!focused && /^(INPUT|SELECT|TEXTAREA)$/.test(focused.tagName);
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
    ['map', 'stats', 'plan', 'compare'].includes(
      new URLSearchParams(location.hash.replace(/^#/, '')).get('view'))
      ? new URLSearchParams(location.hash.replace(/^#/, '')).get('view') : 'list';

  // Changing only the hash is a same-document navigation, so boot() does not
  // re-run. Without this, pasting or editing a filter URL on an already-open
  // page silently does nothing.
  addEventListener('hashchange', () => {
    const p = new URLSearchParams(location.hash.replace(/^#/, ''));
    const one = p.get('r');
    if (one) { openRestaurant(one); return; }   // a single-restaurant link
    // A pasted #season= is a different payload, not a different filter set.
    const code = p.get('season');
    if (code && SEASON && code !== SEASON.code) { switchSeason(code); return; }
    const want = wantedView();
    clearAll(true);
    // The same default boot applies, for the same reason: an absent `by` is
    // "no state yet", not "cleared" -- every URL the page writes says `any`
    // explicitly. Without this, one hand-written link gave 635 rows opened in
    // a fresh tab and 636 typed into the bar of the page already showing.
    if (seasonPhase() !== 'archive') FILTERS.bookableBy = todayISO();
    readHash();
    VIEW = want;          // so the writeHash() inside apply() preserves it
    apply();
    setView(want);        // sync the DOM and open the map if needed
  });

  const wantedRestaurant = () =>
    new URLSearchParams(location.hash.replace(/^#/, '')).get('r');

  VIEW = wantedView();
  apply();
  BOOTED = true;
  setView(VIEW);
  if (wantedRestaurant()) openRestaurant(wantedRestaurant());
}

boot();
