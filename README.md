# NYC Restaurant Week — Summer 2026 tracker

**PRIVATE REPO — must stay private.** The SQLite DB contains menu text extracted
from restaurants' PDFs; publishing that would violate nyctourism.com's terms
(personal/noncommercial use, no republication). See "ToS rules" below before
building anything public-facing from this data.

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
(re-matches from `data/raw/recognition/*.json`) → `diff_report`.

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
- `menu_url` (NULL = restaurant publishes no RW menu PDF — 178 rows) · `website`
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
- `restaurant_slug` · `source` michelin/james_beard/nyt · `level` (michelin:
  `1 star`/`2 stars`/`bib_gourmand`/`recommended`; james_beard:
  `winner`/`nominee`/`semifinalist`; nyt: `nyt_100_best`/`nyt_starred_review`)
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

## Data caveats (read before trusting anything)

- **4 restaurants have NULL address**: alta-calidad, casa-brazilian, catria-nyc,
  wagamama — their nyctourism detail pages 404/500 server-side.
- **17 pending recognition matches** in `data/processed/recognition_review.json`
  (Masa, Roberta's, Tonchin, Ci Siamo, Madre, Lola's, Bar 'Cino, Arcadia, Mắm) —
  awaiting a human ruling; none touch the final-bookings shortlist. Do not
  auto-accept.
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
  Sunday-dinner service (API flags Sunday) · Café Boulud RW posting contested
  between passes; no Sunday service either way · Momofuku Noodle Bar's $45 tier
  is a weekend lunch; ends Aug 30 · Code Red prints $50 (meta $60) vs API $45 —
  unresolved, call first · David Burke Tavern Jul 21–Aug 16 (own nav says 8/14);
  Sunday dinner unprinted · 53's $45 RW Sunday brunch vs their standing $78 dim
  sum — confirm at booking · Chito Gvrito and Playa Betty's print SATURDAY
  service despite the program-wide Saturday exclusion.
- Season windows vary by restaurant (Aug 15/16/30/31, Sep 1/6/7 all observed).

## ToS rules — HARD REQUIREMENTS for any output built from this repo

nyctourism.com terms: personal/noncommercial use; no reproduction/republication
of site content. Applied here as:
1. Any published/derived artifact (dashboard, export, screenshot) uses
   **derived/factual data only**: names, locations, prices, tiers, windows,
   flags, computed gaps, recognition, tag hits.
2. **NO `menus.raw_text`, NO full menu reproduction** anywhere public. Menus are
   linked via the official S3 PDF URL (`restaurants.menu_url`), never mirrored.
   Menu content may appear only as the short `menu_item_tags.matched_text`
   snippets.
3. This repo stays **private** (raw_text + parsed menu items live in the DB).
4. Rate limits stay at ≤1 req/sec against nyctourism hosts for any future crawl.

## Repo layout

```
src/        pipeline (config, fetch_listing, fetch_details, download_menus,
            parse_menus, build_db, tag_dishes, enrich_recognition, diff_report,
            refresh, hours_lookup, price_sweep, price_rescue)
config/     dish_tags.json (tag rules) · shortlist.json (15 booking targets)
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
