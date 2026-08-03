# NYC Restaurant Week — Summer 2026 tracker

**PUBLIC REPO as of 2026-08-02.** Dashboard:
<https://kejjeh.github.io/nyc-restaurant-week/>

This repo was private until 2026-08-02. It was opened after the owner spoke to
NYC Tourism, who said the only violation would be **hosting the exact PDFs** —
extracted text is fine. That conversation is the authority for this change; if
it is ever contradicted, revert to private and re-read "ToS rules" below.

**The one line that must never be crossed: do not host or mirror the menu PDFs.**
`data/raw/menus/*.pdf` is gitignored and 0 PDFs are tracked — keep it that way.
Menus are linked to the official S3 URL, never copied.

Local dataset + pipeline tracking all **648** participants in NYC Restaurant Week
Summer 2026 (Jul 20–Aug 16, extension weeks through Sep 6; Saturdays excluded,
Sundays optional per restaurant). Built to re-run weekly. This README is the full
context for operating the repo — no session memory is assumed.

## Sources & how discovery works

- Listing API: `POST https://program-api.nyctourism.com/restaurant-week`
  `{page, lookup}` → `{items(12/page), total, count, lookup}`. Auth = public
  `x-api-key` embedded in the site's JS; `config.discover_api_key()` re-extracts
  it automatically on 403 (cached in `data/cache/api_key.txt`).
- Addresses: scraped per restaurant from `nyctourism.com/restaurant-week/{slug}/`
  RSC payload (cached in `data/raw/details/`).
- Menu PDFs: public S3 bucket `nyc-tourism-public.s3.amazonaws.com/srw26/menus/`.
- Politeness: global ≤1 req/sec throttle (`config.throttle()`), browser UA.
  robots.txt allows everything used (`/*.json$` is disallowed; the pipeline
  deliberately avoids `_next/data` routes).

## How to run

```
pip install -r requirements.txt        # pdfplumber, playwright
python src/refresh.py                  # weekly refresh + diff report
python src/refresh.py --force-menus    # also re-download PDFs (catches in-place edits)
```

`refresh.py` runs in order: `fetch_listing` (dated snapshot →
`data/raw/listing/snapshot-YYYY-MM-DD.json`; per-page cache makes it resumable) →
`fetch_details` (new slugs only) → `download_menus` (skips cached) →
`parse_menus` (resumable) → `build_db` (rebuilds SQLite + CSV; reloads
`price_sweep` from cached sweep files) → `tag_dishes` → `enrich_recognition`
(re-matches from `data/raw/recognition/*.json`) → `export_site_data` →
`diff_report`.

`export_site_data` MUST stay after both `tag_dishes` and `enrich_recognition`:
`build_db` drops and recreates the DB, wiping the tag and recognition tables.

**Diff report** compares the two latest snapshots: a `SHORTLIST ALERTS` block
first (any change to `config/shortlist.json` restaurants' meal types, weeks, or
menu URL — booking-relevant), then program-wide added/dropped restaurants, field
changes, and menu-PDF content changes by sha256.

**On a fresh clone**, `data/raw/menus/*.pdf` are absent (gitignored);
`download_menus.py` re-fetches all ~470 at ≤1 req/sec (~10 min) on first run.
Everything else needed to rebuild the DB is committed.

Other tools:
- `python src/hours_lookup.py <slug-or-name> [--refresh]` — on-demand opening
  hours for ONE restaurant (own-site JSON-LD, then text heuristics), cached in
  `data/cache/hours.json`. Deliberately never run in bulk.
- `python src/price_sweep.py --report` — ranked heuristic gaps from cached sweep.
- `price_sweep.py --shard=i/k` / `price_rescue.py [--render] --shard=i/k` —
  one-off website price crawls (rescue's `--render` mode needs
  `playwright install chromium`). Results cache per slug in `data/raw/pricesweep/`.

### Dish tags

`config/dish_tags.json` maps tag → regex rules with per-rule confidence.
**Adding a tag = one config entry + `python src/tag_dishes.py`** (no code edits).
Both structured `menu_items` and `menus.raw_text` are scanned so partial parses
are covered; every hit stores the matched snippet for auditing. Seeded tags:
snails (`lumache` = low confidence — usually the pasta shape), braised, confit.

### Season config (winter 2027 changeover)

All season facts live in `src/config.py`: `SEASON = "srw26"` (S3 menu prefix —
VERIFY from live `menuFileUrl` values, don't assume the next prefix), API URL slug
`restaurant-week` (unchanged across seasons). For a new season: update `SEASON`,
clear/rotate `data/raw/listing/` snapshots (diffs across seasons are meaningless),
reset `config/shortlist.json`, expect new week labels in `restaurantInclusionWeek`.
The API key survives seasons and self-rediscovers if rotated.

## Database schema (`data/processed/restaurant_week.sqlite`)

**restaurants** (648 rows) — one per participant
- `slug` PK · `name` · `borough` · `neighborhood` · `address` (NULL for 4, see caveats) · `lat`/`lng`
- `cuisines` JSON array (listing tags minus neighborhood)
- `price_tiers` JSON array, e.g. `["$30","$45"]`
- `meal_periods` JSON array of lunch/dinner/brunch (derived)
- `meal_types_raw` JSON array, verbatim API labels (e.g. `"$60 Sunday Dinner Price"`) — the ground truth `meal_periods`/`sunday_participation` derive from
- `weeks` JSON array, verbatim week labels; last element ⇒ end week
- `sunday_participation` 0/1 (derived: any "Sunday" meal type)
- `menu_url` — **the EMPTY STRING `''` (never NULL) when the restaurant publishes
  no RW menu PDF — 178 rows.** `WHERE menu_url IS NULL` matches ZERO rows; use
  `menu_url = ''`, or branch on whether a `menus` row exists. · `website`
- `reservation_partner`/`reservation_partner_id`/`reservation_link` (link built for OpenTable only)
- `listing_url` · `summary` · `collections` JSON · `snapshot_date`

**menus** (470 rows) — one per downloaded RW menu PDF. **No row for a restaurant
= no menu published (178).** 
- `restaurant_slug` FK · `menu_url` · `pdf_file` · `sha256`
- `parse_quality`: `full` (≥2 course sections + ≥4 dishes extracted — trust
  `menu_items`) / `partial` (text extracted, structure unclear — use `raw_text`)
  / `failed` (PDF exists but is image-only or corrupt — 54 rows; OCR deferred)
- `raw_text` — full extracted text. **Never publish this** (ToS).

**menu_items** (9,233) — structured dishes from `full` parses
- `menu_id` FK · `course` · `dish` · `description` · `supplement_price` · `position`

**menu_item_tags** (304) — dish-tag hits
- `restaurant_slug` · `menu_id` · `menu_item_id` (NULL = hit came from raw_text)
- `tag` · `confidence` high/low · `matched_text` (snippet, audit + display-safe
  short quote) · `source` item/raw_text

**recognition** (195) — external recognition matched to participants
- `restaurant_slug` · `source` michelin/james_beard/nyt · `level` — only **7**
  strings actually occur: michelin `1 star`(6)/`bib_gourmand`(13)/
  `recommended`(38, = Michelin's "The Plate"); james_beard `winner`(30)/
  `nominee`(56)/`semifinalist`(46); nyt `nyt_100_best`(6). `2 stars` and
  `nyt_starred_review` exist in the RAW files but no such restaurant
  participates — don't assume they render, don't crash if a future snapshot
  adds them.
- `year` · `source_url` · `matched_name` · `notes`
- `match_confidence`: 1.0 = exact name + street-number match; 0.9 = exact unique
  name, no address available (ALL james_beard rows — JBF publishes no addresses);
  0.8 = fuzzy name + address. Ambiguous cases were NOT inserted — they're in
  `data/processed/recognition_review.json` (see caveats).
- Raw source data: `data/raw/recognition/*.json` (committed — not regenerable by
  the pipeline; JB award names live only there).

**price_sweep** (600) — automated website price triage
- `restaurant_slug` · `comparable_3course` (median-stack estimate, $) ·
  `gaps` JSON `{"$45": 14, ...}` (comparable minus RW tier) · `confidence`
  high/medium/low/none · `pages_fetched` · `n_prices` · `error` · `swept_date`
- `comparable_basis` = `'heuristic'` on every row: **triage-grade, never
  verified**. Known biases: understates expensive kitchens (mains capped at $70),
  overstates casual formats. Hand-verified findings live in `reports/`, not here.

## Reports (`reports/`)

- `rw-final-bookings.md` — **the decision doc**: 15 ranked bookings with
  own-site-verified windows, gaps, booking links, BOOK-BY flags. Supersedes the
  value report for decisions.
- `rw-summer-2026-value-report.md` — archive: full value analysis, traps,
  evidence grades ([A] own menu … [D] uncorroborated), 3 addenda, source tables.

## Dashboard (`docs/`, served by GitHub Pages)

Static sort/filter dashboard over the 648 participants. No backend, no build
step: `docs/index.html` + `docs/app.js` + `docs/styles.css` read
`docs/data/restaurants.json`. Preview locally with
`python -m http.server 8137 --directory docs` (a `file://` open will NOT work —
the payload is fetched).

Navigating it: **quick views** (★ Saved / The 15 / Closing soon / Verified gaps
/ Sunday / Michelin) each set filters *and* sort in one tap and are toggles —
tapping the active one returns you to everything. Applied filters show as
removable chips, so you never have to open the panel to see what's on. **★ Save**
(in each row's detail) keeps your own shortlist in `localStorage`, separate from
the curated ranking; the Saved view only appears once you've saved something.

Only the search box, Filters and Sort are sticky — pinning the quick views and
counts too left 43% of a phone screen for content. Rows render 50 at a time
(auto-loading on scroll, `Show more` as the keyboard path), keeping the page
~1,600 DOM nodes instead of ~14,500. Sorting by end date groups rows under
closing-date headings. The filter panel has a **find-a-filter** box that
searches all groups at once, and long facets (76 neighborhoods, 56 cuisines)
collapse to the 12 most populated with a `Show N more`. `/` focuses search,
`Esc` clears it or closes the panel. Filter state lives in the URL hash, so any
view can be bookmarked.

**Four views.** `List` · `Map` · `Stats` (headline tiles + four click-to-filter
SVG charts, no charting library, describing the whole programme) · `Plan`.

**Plan** turns the ★ shortlist into dated bookings. Each restaurant's date
picker offers ONLY days it can actually serve you: inside its window, never a
Saturday unless it carries the `saturday_service` flag, and a Sunday only where
Sunday service is established (so Mark's Off Madison offers none despite the
API's Sunday claim, while Café Boulud is the only one of the shortlist offering
Saturdays). Assignments live in `localStorage`, and an assignment that becomes
impossible is dropped on the next render.

**Subway proximity.** `python src/fetch_subway.py` pulls MTA station locations
once from NY Open Data (public domain, dataset `39hk-dx4f`) into
`data/raw/subway/stations.json` — committed, and deliberately NOT re-fetched by
`refresh.py`. The exporter attaches, per restaurant, every route within a
12-minute walk plus the nearest station. Drives a `Subway line` facet, a
`4/5/6` quick view (340 restaurants), and a `Subway walk` sort that measures to
whichever lines you've selected. Distances are straight-line × 1.3 at 80 m/min
— an approximation, labelled `~` in the UI, not a routing engine.

**Map view** (the `Map` button) plots the current filtered set, colour-coded by
gap basis with closing-soon in red; markers follow the filters, popups link
straight to booking, and `Details` jumps back to that row in the list.

> **One architectural exception.** Everything else here is dependency-free, but
> the map loads **Leaflet 1.9.4 from unpkg** and **CARTO basemap tiles** — the
> only third-party assets on the site. They are fetched *lazily, on first map
> open*, so the list view still makes **zero external requests**, and if either
> is unreachable the map shows an explanatory message while the list keeps
> working. A self-contained map would need committed borough geometry and would
> still have no streets, which is most of what makes a map useful here.

`src/export_site_data.py` builds the payload (`--check` validates and prints
without writing). It merges, in this precedence order:

1. **`config/verified_values.json`** — hand-verified facts transcribed from
   `reports/rw-final-bookings.md` and the caveat list below. The restaurant's
   own printed materials always win over the listing API. 23 entries: the 15
   ranked bookings plus 8 verified caveat rows.
2. **listing API fields** from `restaurants` (end date parsed from the last
   `weeks` label).
3. **`price_sweep`** — heuristic only, always rendered as an "estimate", never
   allowed to populate a verified field.

`config/recognition_suppress.json` drops recognition rows that are in the DB but
demonstrably wrong (see caveats). Set `active: false` to restore them.

**ToS enforcement is in code, not convention.** `assert_tos_clean()` fails the
export rather than publish a banned field. Menu text may leave only as dish-tag
snippets, and the rule is: **at most 5% of a menu's extracted text, or 40
characters, whichever is greater**, re-centred on the matched keyword (never
right-truncated), with overlapping and adjacent snippets pruned so they cannot
be stitched back into a passage. The published payload carries no `raw_text`,
no `menu_items`, and no dish/description fields.

Coverage today: 648 rows · 14 verified gaps · 345 estimates · 289 with no
comparable (left blank — never backfilled) · 120 restaurants with dish tags ·
78 with recognition badges. Payload ~780 KB raw, ~76 KB gzipped.

## Data caveats (read before trusting anything)

- **4 restaurants have NULL address**: alta-calidad, casa-brazilian, catria-nyc,
  wagamama — their nyctourism detail pages 404/500 server-side.
- **17 pending recognition matches** in `data/processed/recognition_review.json`
  (Masa, Roberta's, Tonchin, Ci Siamo, Madre, Lola's, Bar 'Cino, Arcadia, Mắm) —
  awaiting a human ruling; none touch the final-bookings shortlist. Do not
  auto-accept.
- **10 recognition rows in the DB are FALSE POSITIVES** (distinct from the 17
  pending above — these were auto-accepted at match time). `norm_name()` in
  `enrich_recognition.py` strips a stopword list before comparing, and where
  stripping collapses two different restaurants onto one string the match still
  scores 0.9 with notes `' [exact name]'`. So **0.9 + "[exact name]" does NOT
  mean the names resemble each other.** Confirmed cases: `osteria-brooklyn`
  absorbed all 8 James Beard awards belonging to the Seagram Building's
  *Brasserie* (both normalise to the EMPTY STRING); `cafe-boulud` absorbed 2
  nominations belonging to *Bar Boulud* (both normalise to `boulud`). Listed in
  `config/recognition_suppress.json` and excluded from the dashboard; the DB is
  untouched. **A real fix belongs in `norm_name()`** — it will re-occur on every
  `enrich_recognition` run.
- **`config/shortlist.json` ranks 3–5 disagree with the decision doc.** The
  report ranks David Burke Tavern 3, Zuma 4, Casa Lever 5; `shortlist.json` has
  Casa Lever 3, David Burke Tavern 4, Zuma 5. The other 12 match. The dashboard
  uses the REPORT order (it is the decision doc and supersedes). Reconcile the
  two files when convenient.
- **~240 restaurants have no price comparable** (publish no prices anywhere).
  HARD RULE: never backfill from delivery aggregators — markup distortion makes
  them worse than an honest blank. The gap stays a gap.
- **~290 comparables are heuristic-only** (`price_sweep`, `comparable_basis=
  'heuristic'`) — nothing downstream may present them as verified.
- **OCR of the 54 image-only menus: deferred**, not attempted.
- **Listing-vs-restaurant contradictions** (restaurant's own print wins; found by
  two independent own-site passes, Aug 1): Zuma ends Aug 16 (API: Sep 6) ·
  Lincoln Tue–Sat only, ends Aug 15 or 16 (sources 1 day apart), no Sunday
  despite API · Ai Fiori & Tao Uptown Mon–Fri (API says +Sunday) · Le Pavillon is
  LUNCH-only, $60 = 2 courses (API implies $60 dinner) · Four Twenty Five
  extended to Sep 6 · Tudor City's site shows stale winter pages; its $30 lunch
  exists only in the API — dropped from bookings · Mark's Off Madison has no
  Sunday-dinner service (API flags Sunday) · Café Boulud RW posting resolved (JS-rendered page): $60 3-course, Mon–Thu 6–9 + Fri–Sat 5–9:30 — printed Saturday RW service; no Sunday · Momofuku Noodle Bar's $45 tier
  is a weekend lunch; ends Aug 30 · Code Red SETTLED at $50 by live page read (API $45 wrong; $60 stale metadata) · David Burke Tavern Jul 21–Aug 16 (own nav says 8/14);
  Sunday dinner unprinted · 53's $45 RW Sunday brunch vs their standing $78 dim
  sum — confirm at booking · Chito Gvrito and Playa Betty's print SATURDAY
  service despite the program-wide Saturday exclusion.
- Season windows vary by restaurant (Aug 15/16/30/31, Sep 1/6/7 all observed).
- **3 restaurants geocode outside New York.** `dubuhaus` and `musaek` (both
  6 E. 32nd St., Manhattan) resolve to Oakland CA, and `the-kunjip`
  (32 W. 32nd St.) to San Angelo TX — bad lat/lng in the nyctourism detail
  pages, not a parsing error. `sane_coords()` in `export_site_data.py` nulls
  any point outside a five-borough bounding box, so the map neither misplaces
  them nor lets one bad point wreck its auto-fit. With the 2 NULL-address rows
  that leaves 640 of 645 mappable.

## ToS rules — HARD REQUIREMENTS for any output built from this repo

Position as of **2026-08-02**, per the owner's conversation with NYC Tourism:
extracted menu TEXT is acceptable; **hosting the exact PDFs is not**.

1. **NEVER host, mirror, re-upload or commit the menu PDFs.** This is the only
   hard prohibition NYC Tourism named. `data/raw/menus/*.pdf` stays gitignored;
   menus are linked via the official S3 URL (`restaurants.menu_url`). Verify
   with `git ls-files | grep -c '\.pdf$'` — it must print 0.
2. Rate limits stay at **≤1 req/sec** against nyctourism hosts and the S3
   bucket for any future crawl (`config.throttle()`). Do not parallelise.
3. Personal/noncommercial use. `docs/` keeps `<meta name="robots" content=
   "noindex">` — this is a personal tool, not a publication.
4. The dashboard payload (`docs/data/restaurants.json`) still ships
   **derived/factual fields only** and carries no `raw_text`, no `menu_items`
   and no dish/description fields. That is now a design choice for payload size
   and honesty rather than a ToS requirement, and `assert_tos_clean()` in
   `src/export_site_data.py` still enforces it. Menu text appears only as short
   keyword-centred snippets (≤5% of a menu, or 40 chars, whichever is greater).

Superseded: before 2026-08-02 this section required the repo to stay private
because `menus.raw_text` lives in the committed SQLite. That requirement is
lifted by the NYC Tourism conversation above; rule 1 is not.

## Repo layout

```
src/        pipeline (config, fetch_listing, fetch_details, download_menus,
            parse_menus, build_db, tag_dishes, enrich_recognition,
            export_site_data, diff_report, refresh, hours_lookup, price_sweep,
            price_rescue)
config/     dish_tags.json (tag rules) · shortlist.json (15 booking targets) ·
            verified_values.json (hand-verified facts, merged over the API) ·
            recognition_suppress.json (known-bad recognition matches)
docs/       the dashboard (index.html, app.js, styles.css, data/restaurants.json)
.github/    workflows/refresh.yml — Monday 07:00 ET cron + manual dispatch
data/raw/   listing snapshots+page cache · details (addresses) · recognition
            (Michelin/JB/NYT source data) · pricesweep (heuristic crawl cache) ·
            menus/manifest.json + parsed.json (PDFs themselves are gitignored)
data/processed/  restaurant_week.sqlite · restaurants.csv ·
                 recognition_review.json (pending human rulings)
reports/    final bookings (decision doc) + value report (archive)
```

Known environment quirk (Cowork sandbox only): the mounted filesystem blocks
`unlink`, which strands git `index.lock` files and sqlite `-journal` files —
delete them and retry. Normal machines are unaffected. `build_db.py` and other
DB writers build in a temp dir and copy into place for the same reason; keep
that pattern.
