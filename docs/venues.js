/* NYC award-recognised restaurants — roster
 *
 * Vanilla JS, no build step, no framework. Renders docs/data/venues.json.
 *
 * The dashboard next door (app.js) ranks 636 restaurants by what their prix
 * fixe is worth. This page ranks 1,400 by what juries have said about them,
 * which is a different question with different data behind it: most of these
 * restaurants have no menu here, no price, and — until someone spends a Places
 * lookup on them — no confirmed address. So every row has to stay legible when
 * most of its fields are absent, and absent must never render as zero.
 *
 * Two traps carried over from app.js, because the data is the same shape:
 *   1. One slug is "53". Plain objects coerce integer-like keys and reorder
 *      them ahead of string keys, so slug lookups here use a Map.
 *   2. Names carry diacritics ("Café Boulud", "Mắm"). Search folds them on both
 *      sides so "mam" finds "Mắm".
 */
'use strict';

const DATA_URL = 'data/venues.json';
const PAGE = 120;
/* Values listed per filter group before the group says how many it is hiding. */
const FACET_LIMIT = 14;

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

const STATE = {
  data: null,
  rows: [],
  bySlug: new Map(),
  q: '',
  sort: 'prestige',
  filters: new Map(),   // facet -> Set(values)
  shown: PAGE,
};

/* ---------- scoring helpers -------------------------------------------- */

/* A 4.9 from 30 people is not better than a 4.5 from 3,000, so the rating sort
   shrinks each score toward the roster mean by how thin its sample is. Same
   treatment export_places.py gives the dashboard's ratings, and for the same
   reason: an unweighted star sort puts the least-known rooms on top. */
const PRIOR = 300;
let RATING_MEAN = 4.4;

function weightedRating(v) {
  if (v.rating == null) return null;
  const n = v.ratings_total || 0;
  return (v.rating * n + RATING_MEAN * PRIOR) / (n + PRIOR);
}

/* ---------- facets ------------------------------------------------------ */

/* Every honour a venue holds, in the payload's own English.
   `top_honor_label` is the single HIGHEST one, which is a different question:
   Daniel holds a Michelin star, but its highest honour is a Beard win, because
   a Beard win scores 88 and one star scores 84 (config/awards.json). Filtering
   on the highest honour therefore hid 7 of the 69 starred restaurants, 12 of
   the 80 Bib Gourmands, 8 of the 194 Beard winners, and 24 of the NYT's own
   Top 100 — a control named "NYT Top 100" that returned 76 rows.
   app.js made this call correctly for the dashboard already ("By TIER, not by
   source"); this is the roster catching up. */
function honoursHeld(v) {
  if (v._honours) return v._honours;
  const out = [];
  for (const a of v.recognition) {
    const h = STATE.data.honors[`${a.source}:${a.level}`];
    if (h && h.label && !out.includes(h.label)) out.push(h.label);
  }
  v._honours = out;
  return out;
}

const FACETS = [
  { key: 'top_honor_label', label: 'Highest honour',
    get: (v) => (v.top_honor_label ? [v.top_honor_label] : []) },
  /* Not a duplicate of the group above: this one answers "has it ever been
     given X", the other "what is the best it holds". A venue appears once in
     the first and once per honour here. */
  { key: 'honour_held', label: 'Honours held (any)', get: honoursHeld },
  { key: 'award_source', label: 'Named by',
    get: (v) => v.award_sources.map((s) => STATE.data.source_labels[s] || s) },
  { key: 'borough', label: 'Borough', get: (v) => (v.borough ? [v.borough] : []) },
  { key: 'status', label: 'Still trading',
    get: (v) => [{ open: 'Open', closed: 'Closed', unknown: 'Unverified' }[v.status]] },
  { key: 'rw', label: 'Restaurant Week',
    get: (v) => [v.rw ? 'In this season' : 'Not participating'] },
  { key: 'cuisine', label: 'Cuisine (Restaurant Week rows only)',
    get: (v) => (v.rw ? v.rw.cuisines : []) },
  /* The only thing on the roster that answers "what does this place actually
     cook". Parsed from Restaurant Week menus, so it exists for those rows and
     not for the 778 that were never in the programme. */
  { key: 'dish', label: 'On the menu (Restaurant Week rows only)',
    get: (v) => v.dishes || [] },
];

/* Every preset here names an honour ("Michelin starred", "NYT Top 100"), so
   every one of them filters on honours HELD. None of them means "and nothing
   better" — which is what filtering on `top_honor_label` quietly meant. */
const PRESETS = [
  { label: 'Michelin starred', apply: () => setFilter('honour_held',
      ['One Michelin star', 'Two Michelin stars', 'Three Michelin stars']) },
  { label: 'Bib Gourmand', apply: () => setFilter('honour_held', ['Bib Gourmand']) },
  { label: 'NYT Top 100', apply: () => setFilter('honour_held', ['NYT Top 100']) },
  { label: 'Beard winners', apply: () => setFilter('honour_held', ['James Beard winner']) },
  { label: 'All three juries agree', apply: () => { clearFilters(); STATE.threeWay = true; } },
  { label: 'In Restaurant Week', apply: () => setFilter('rw', ['In this season']) },
  { label: 'Open', apply: () => setFilter('status', ['Open']) },
  { label: 'Game, offal & odd cuts',
    apply: () => setFilter('dish', ['game meats', 'foie gras', 'bone marrow',
                                    'sweetbreads', 'snails', 'steak tartare']) },
];

function setFilter(key, values) {
  clearFilters();
  STATE.filters.set(key, new Set(values));
}
function clearFilters() {
  STATE.filters.clear();
  STATE.threeWay = false;
}

/* ---------- filtering + sorting ----------------------------------------- */

function haystack(v) {
  if (v._hay) return v._hay;
  const parts = [v.name, v.borough, v.neighborhood, v.address, v.top_honor_label];
  for (const a of v.recognition) parts.push(a.award, a.person, a.level);
  if (v.rw) parts.push(...v.rw.cuisines);
  if (v.dishes) parts.push(...v.dishes);
  if (v.dishes_maybe) parts.push(...v.dishes_maybe);
  v._hay = fold(parts.filter(Boolean).join(' '));
  return v._hay;
}

/* `exceptKey` leaves one facet out of the test, which is what lets that facet
   count itself honestly. Counting against the FULLY filtered set would zero
   every unselected value in the facet you just used, so a second choice in the
   same group becomes unreachable — you could never say "Brooklyn OR Queens".
   Same rule the dashboard uses, and for the same reason. */
function matches(v, exceptKey) {
  if (STATE.q && !haystack(v).includes(STATE.q)) return false;
  if (STATE.threeWay && v.award_sources.length < 3) return false;
  for (const [key, chosen] of STATE.filters) {
    if (key === exceptKey) continue;
    const facet = FACETS.find((f) => f.key === key);
    const mine = facet.get(v);
    if (!mine.some((m) => chosen.has(m))) return false;
  }
  return true;
}

const SORTS = {
  prestige: (a, b) => b.prestige - a.prestige || b.award_count - a.award_count
                      || a.name.localeCompare(b.name),
  recent: (a, b) => (b.last_award_year || 0) - (a.last_award_year || 0)
                    || b.prestige - a.prestige,
  awards: (a, b) => b.award_count - a.award_count || b.prestige - a.prestige,
  /* Nulls sort last in every direction: a restaurant nobody has rated is not
     the worst-rated one. */
  rating: (a, b) => {
    const x = weightedRating(a), y = weightedRating(b);
    if (x == null && y == null) return b.prestige - a.prestige;
    if (x == null) return 1;
    if (y == null) return -1;
    return y - x;
  },
  name: (a, b) => a.name.localeCompare(b.name),
  oldest: (a, b) => {
    const x = a.first_award_year, y = b.first_award_year;
    if (x == null && y == null) return b.prestige - a.prestige;
    if (x == null) return 1;
    if (y == null) return -1;
    return x - y;
  },
};

/* ---------- rendering --------------------------------------------------- */

const HONOR_CLASS = (key) =>
  !key ? 'none' : key.startsWith('michelin') ? 'michelin'
    : key.startsWith('nyt') ? 'nyt' : 'beard';

function statusPill(v) {
  const label = { open: 'Open', closed: 'Closed', unknown: 'Unverified' }[v.status];
  const pill = el('span', `pill status-${v.status}`, label);
  pill.title = v.status_source
    ? `Status from ${v.status_source}`
    : 'Nothing has confirmed whether this restaurant is still trading. '
      + 'That is a gap in our data, not a claim that it closed.';
  return pill;
}

function awardLine(a) {
  const li = el('li', 'awardRow');
  li.append(el('span', 'awardYear', a.year == null ? '—' : String(a.year)));
  /* Michelin and the Times store the honour in `level` as a machine value
     ("3 stars", "nyt_100_best"); config/awards.json is where those get their
     English, and the payload carries that map so this file never spells an
     honour itself. The Beard rows already read as prose, so they keep theirs. */
  const honour = STATE.data.honors[`${a.source}:${a.level}`];
  const what = a.award
    ? `${a.award} · ${a.level}`
    : (honour ? honour.label : a.level) || 'recognised';
  const label = a.rank ? `${what} (no. ${a.rank})` : what;
  if (a.url) {
    const link = el('a', 'awardWhat', label);
    link.href = a.url;
    link.rel = 'noreferrer noopener';
    link.target = '_blank';
    li.append(link);
  } else {
    li.append(el('span', 'awardWhat', label));
  }
  /* The Beard awards are frequently to a chef rather than to a room, and
     hiding that makes the roster claim the restaurant won things it did not. */
  if (a.person) li.append(el('span', 'awardWho', a.person));
  return li;
}

function renderRow(v) {
  const row = el('article', 'venue');

  const head = el('div', 'venueHead');
  const title = el('h2', 'venueName', v.name);
  head.append(title);
  if (v.top_honor_label) {
    head.append(el('span', `pill honor ${HONOR_CLASS(v.top_honor)}`, v.top_honor_label));
  }
  head.append(statusPill(v));
  if (v.rw) {
    const tiers = v.rw.price_tiers.join(' / ') || 'Restaurant Week';
    const rw = el('a', 'pill rw', `Restaurant Week ${tiers}`);
    /* Straight to this restaurant on the dashboard, not to the top of it. The
       dashboard reads `#r=<slug>` and openRestaurant() clears whatever was
       filtered so the link wins -- landing someone on a 636-row list and
       leaving them to find the name again is not a link, it is a hint. */
    rw.href = `restaurant-week.html#r=${encodeURIComponent(v.rw.slug)}`;
    rw.title = 'Open this restaurant on the value dashboard — its prix fixe, '
             + 'menu, gap against à la carte and subway walk.';
    head.append(rw);
  }
  row.append(head);

  const meta = el('p', 'venueMeta');
  const place = [v.neighborhood, v.borough].filter(Boolean).join(', ')
             || v.address || 'location not established';
  meta.append(el('span', 'where', place));
  if (v.rating != null) {
    meta.append(el('span', 'rating',
      `${v.rating.toFixed(1)}★ (${(v.ratings_total || 0).toLocaleString()})`));
  }
  const span = v.first_award_year == null ? null
    : v.first_award_year === v.last_award_year ? String(v.first_award_year)
    : `${v.first_award_year}–${v.last_award_year}`;
  if (span) meta.append(el('span', 'era', `recognised ${span}`));
  if (v.award_sources.length > 1) {
    meta.append(el('span', 'juries', `${v.award_sources.length} juries`));
  }
  /* The one thing a person actually wants to DO with a row. Only the 636
     Restaurant Week rows carry a link -- the award files have no websites in
     them -- so this renders for those and is simply absent for the rest,
     rather than a dead control that looks the same for everyone. */
  if (v.dishes && v.dishes.length) {
    /* Six is where the line wraps on a phone; the row says how many it kept
       back rather than trailing off. */
    const head = v.dishes.slice(0, 6);
    const rest = v.dishes.length - head.length;
    const d = el('span', 'dishes', head.join(' · ') + (rest ? ` +${rest}` : ''));
    d.title = "Matched on this restaurant's Restaurant Week menu: "
            + v.dishes.join(', ');
    meta.append(d);
  }
  /* The weaker claim, marked rather than blended in — the same grammar the
     dashboard uses for an estimated price. The word is on the menu, but the
     dish may not be about it: most of these are truffle honey or truffle mayo
     rather than a truffle dish. Filters ignore these; search still finds them. */
  if (v.dishes_maybe && v.dishes_maybe.length) {
    const m = el('span', 'dishesMaybe', v.dishes_maybe.slice(0, 4).join(' · ') + '?');
    m.title = 'Mentioned on the menu, but as a garnish or in passing rather '
            + 'than as the dish: ' + v.dishes_maybe.join(', ')
            + '. The filters deliberately do not count these.';
    meta.append(m);
  }
  if (v.rw && v.rw.reserve) {
    const book = el('a', 'reserve', 'Book');
    book.href = v.rw.reserve;
    book.rel = 'noreferrer noopener';
    book.target = '_blank';
    book.title = `Reservations or website for ${v.name}`;
    meta.append(book);
  }
  row.append(meta);

  if (v.recognition.length) {
    const det = el('details', 'awards');
    det.append(el('summary', null,
      `${v.award_count} award record${v.award_count === 1 ? '' : 's'}`));
    const list = el('ul', 'awardList');
    for (const a of v.recognition) list.append(awardLine(a));
    det.append(list);
    row.append(det);
  }
  return row;
}

function renderFacets() {
  const box = $('#facets');
  box.textContent = '';
  for (const f of FACETS) {
    /* Counted against what is actually on screen, not against the whole
       roster. Filtered to the 62 Michelin-starred venues, this group used to
       still say "Manhattan 1104" — a count is a promise about what clicking
       will give you, and that one could promise 55 rows and deliver none. */
    const counts = new Map();
    for (const v of STATE.rows) {
      if (!matches(v, f.key)) continue;
      for (const val of f.get(v)) {
        if (val) counts.set(val, (counts.get(val) || 0) + 1);
      }
    }
    // A ticked value stays on screen at zero, or there is no way to untick it.
    for (const val of (STATE.filters.get(f.key) || [])) {
      if (!counts.has(val)) counts.set(val, 0);
    }
    if (counts.size < 2 && !STATE.filters.has(f.key)) continue;
    const group = el('div', 'facet');
    group.append(el('h3', null, f.label));
    const chosen = STATE.filters.get(f.key);
    const sorted = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    /* Cuisine has far more values than fit, so the list is cut -- but a cut
       list that does not say it was cut reads as "those are all of them", and
       someone looking for Georgian would conclude the roster has none. Any
       chosen value is kept regardless of where it sorts, or ticking it and
       then searching would make the tick vanish. */
    const shown = sorted.slice(0, FACET_LIMIT);
    const shownVals = new Set(shown.map(([v]) => v));
    for (const [val, n] of sorted) {
      if (chosen && chosen.has(val) && !shownVals.has(val)) shown.push([val, n]);
    }
    const hidden = sorted.length - shown.length;
    for (const [val, n] of shown) {
      const id = `f-${f.key}-${fold(val).replace(/\W+/g, '-')}`;
      const label = el('label', 'facetOpt');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.id = id;
      cb.checked = Boolean(chosen && chosen.has(val));
      cb.addEventListener('change', () => {
        const set = STATE.filters.get(f.key) || new Set();
        if (cb.checked) set.add(val); else set.delete(val);
        if (set.size) STATE.filters.set(f.key, set); else STATE.filters.delete(f.key);
        STATE.shown = PAGE;
        apply();
      });
      label.append(cb, el('span', 'facetName', val), el('span', 'facetN', String(n)));
      group.append(label);
    }
    if (hidden > 0) {
      const note = el('p', 'facetMore',
        `+${hidden} more — use the search box`);
      note.title = `${sorted.length} values in total; the ${shown.length} `
                 + `commonest are listed. Search matches all of them.`;
      group.append(note);
    }
    box.append(group);
  }
}

function renderPresets() {
  const box = $('#presets');
  box.textContent = '';
  for (const p of PRESETS) {
    const b = el('button', 'preset', p.label);
    b.type = 'button';
    b.addEventListener('click', () => { p.apply(); STATE.shown = PAGE; apply(); });
    box.append(b);
  }
}

function renderActive() {
  const box = $('#activeFilters');
  box.textContent = '';
  const chips = [];
  if (STATE.threeWay) chips.push(['Named by all three juries', () => { STATE.threeWay = false; }]);
  for (const [key, set] of STATE.filters) {
    for (const val of set) {
      chips.push([val, () => {
        set.delete(val);
        if (!set.size) STATE.filters.delete(key);
      }]);
    }
  }
  box.hidden = chips.length === 0;
  for (const [label, undo] of chips) {
    const chip = el('button', 'chip', `${label} ✕`);
    chip.type = 'button';
    chip.addEventListener('click', () => { undo(); STATE.shown = PAGE; apply(); });
    box.append(chip);
  }
  const n = chips.length;
  $('#filterCount').textContent = String(n);
  $('#filterCount').hidden = n === 0;
  $('#clearBtn').hidden = n === 0;
}

function apply() {
  const hits = STATE.rows.filter(matches);
  hits.sort(SORTS[STATE.sort] || SORTS.prestige);

  const box = $('#rows');
  box.textContent = '';
  for (const v of hits.slice(0, STATE.shown)) box.append(renderRow(v));

  $('#shown').textContent = String(Math.min(STATE.shown, hits.length));
  $('#total').textContent = String(hits.length);
  $('#empty').hidden = hits.length !== 0;
  const more = $('#showMore');
  more.hidden = hits.length <= STATE.shown;
  more.textContent = `Show ${Math.min(PAGE, hits.length - STATE.shown)} more`;
  renderActive();
  /* Always, now that the counts depend on the filters. Re-rendering steals
     focus from the checkbox you just used, so it is handed straight back. */
  const focused = document.activeElement && document.activeElement.id;
  renderFacets();
  if (focused) {
    const again = document.getElementById(focused);
    if (again) again.focus();
  }
}

function renderCoverage() {
  const c = STATE.data.counts;
  const box = $('#coverage');
  box.hidden = false;
  const p = el('p');
  p.append(el('strong', null, `${c.with_recognition.toLocaleString()} recognised restaurants`));
  p.append(document.createTextNode(
    `, ${c.in_restaurant_week} of them in ${STATE.data.season_label} Restaurant Week `
    + `(${c.both} are both). `));
  if (c.unverified) {
    p.append(document.createTextNode(
      `${c.unverified.toLocaleString()} have no confirmed open/closed status yet — `
      + `they are shown as Unverified rather than assumed open.`));
  }
  box.append(p);
}

/* ---------- boot -------------------------------------------------------- */

function wire() {
  const q = $('#q');
  q.addEventListener('input', () => {
    STATE.q = fold(q.value.trim());
    STATE.shown = PAGE;
    apply();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement !== q) {
      e.preventDefault();
      q.focus();
    }
  });
  $('#sort').addEventListener('change', (e) => { STATE.sort = e.target.value; apply(); });
  $('#filterBtn').addEventListener('click', () => {
    const panel = $('#panel');
    panel.hidden = !panel.hidden;
    $('#filterBtn').setAttribute('aria-expanded', String(!panel.hidden));
  });
  $('#showMore').addEventListener('click', () => { STATE.shown += PAGE; apply(); });
  for (const id of ['#clearBtn', '#clearBtn2']) {
    $(id).addEventListener('click', () => {
      clearFilters();
      STATE.q = '';
      $('#q').value = '';
      STATE.shown = PAGE;
      apply();
    });
  }
  const toTop = $('#toTop');
  toTop.addEventListener('click', () => window.scrollTo({ top: 0 }));
  window.addEventListener('scroll', () => { toTop.hidden = window.scrollY < 800; },
                          { passive: true });

  /* Same key the dashboard uses, so a viewer's choice survives the hop. */
  let saved = null;
  try { saved = localStorage.getItem('rw-theme'); } catch { /* private mode */ }
  if (saved) document.documentElement.dataset.theme = saved;
  $('#themeToggle').addEventListener('click', () => {
    const cur = document.documentElement.dataset.theme
      || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('rw-theme', next); } catch { /* private mode */ }
  });
}

async function boot() {
  let payload;
  try {
    const res = await fetch(DATA_URL, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    payload = await res.json();
  } catch (err) {
    $('#rows').append(el('p', 'empty',
      `Could not load the roster (${err.message}). The data file is docs/data/venues.json.`));
    return;
  }
  STATE.data = payload;
  STATE.rows = payload.venues;
  for (const v of STATE.rows) STATE.bySlug.set(v.slug, v);

  const rated = STATE.rows.filter((v) => v.rating != null);
  if (rated.length) {
    RATING_MEAN = rated.reduce((s, v) => s + v.rating, 0) / rated.length;
  }

  $('#rosterCount').textContent = `${payload.counts.venues.toLocaleString()} restaurants`;
  $('#footProvenance').textContent =
    `Built ${payload.generated_at.slice(0, 10)} from the Michelin 2025 NYC selection, `
    + `James Beard Foundation awards 1991–2026, and the New York Times Top 100. `
    + `${payload.counts.mappable.toLocaleString()} of ${payload.counts.venues.toLocaleString()} `
    + `venues have confirmed coordinates.`;

  renderCoverage();
  renderPresets();
  wire();
  apply();
}

boot();
