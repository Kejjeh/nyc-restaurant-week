# NYC Restaurant Week — Summer 2026 tracker

Local dataset of every participating restaurant in NYC Restaurant Week Summer 2026
(Jul 20–Aug 16, extension weeks through Sep 6), with offerings, menu PDFs, prices,
and participation details. Built to be re-run weekly.

## Source

- Listing: `POST https://program-api.nyctourism.com/restaurant-week` `{page, lookup}`
  — the JSON API behind the participant browser at nyctourism.com/restaurant-week.
  Auth is a public `x-api-key` embedded in the site's client JS; if it rotates,
  `config.discover_api_key()` re-extracts it automatically.
- Addresses: detail pages `nyctourism.com/restaurant-week/{slug}/` (RSC payload).
- Menus: public S3 bucket `nyc-tourism-public.s3.amazonaws.com/srw26/menus/…`
  (season prefix `srw26`, verified from live listing data).

Politeness: all requests share a global ≤1 req/sec throttle and a normal browser
UA. robots.txt permits these paths (`/*.json$` is disallowed, which is why the
pipeline uses the program-api endpoint and HTML detail pages, not `_next/data`
routes). Terms limit use to personal/noncommercial — don't republish the data.

## Re-run

```
pip install -r requirements.txt
python src/refresh.py                # weekly refresh + diff vs previous snapshot
python src/refresh.py --force-menus  # also re-download PDFs to catch in-place menu edits
```

`refresh.py` runs, in order: `fetch_listing` (dated snapshot →
`data/raw/listing/snapshot-YYYY-MM-DD.json`), `fetch_details` (address cache,
new slugs only), `download_menus` (skips already-downloaded PDFs),
`parse_menus`, `build_db`, `diff_report` (added/dropped/changed vs prior
snapshot, plus menu-PDF content changes by sha256).

Individual steps can be run standalone: `python src/fetch_listing.py`, etc.

## On-demand hours

Hours are deliberately not bulk-enriched. For one restaurant:

```
python src/hours_lookup.py manhatta          # by slug
python src/hours_lookup.py "La Contenta"     # by name
python src/hours_lookup.py manhatta --refresh
```

Results cache to `data/cache/hours.json`.

## Outputs (`data/processed/`)

- `restaurant_week.sqlite`
  - `restaurants` — one row per participant: name, borough, neighborhood,
    address, lat/lng, cuisines, price_tiers ($30/$45/$60), meal_periods,
    meal_types_raw (verbatim), weeks, sunday_participation, menu_url, website,
    reservation partner/id/link, listing_url, summary, collections.
  - `menus` — one row per downloaded PDF: url, file, sha256,
    `parse_quality` (`full` = ≥2 courses & ≥4 dishes detected; `partial` =
    text extracted, structure unclear; `failed` = image-only/corrupt),
    `raw_text` (always kept, so nothing is lost on bad parses).
  - `menu_items` — course / dish / description / supplement_price rows for
    structured parses.
- `restaurants.csv` — flat export for quick filtering.

## Layout

```
src/            pipeline code (config, fetch_listing, fetch_details,
                download_menus, parse_menus, build_db, diff_report, refresh,
                hours_lookup)
data/raw/       listing snapshots, per-slug detail JSON, menu PDFs + manifest
data/processed/ SQLite + CSV
data/cache/     api key + hours cache (gitignored)
```

## Dish tags

`config/dish_tags.json` maps tag -> regex rules (with per-rule confidence).
Adding a tag is a one-line config change + `python src/tag_dishes.py`.
Hits land in `menu_item_tags` with the matched snippet for auditing; both
structured `menu_items` and `menus.raw_text` are scanned, so partial parses
are covered. Seeded: snails (lumache = low confidence — usually the pasta),
braised, confit.

## Recognition

`recognition` table: Michelin (stars/Bib/recommended), James Beard
(winners/nominees/semifinalists, 1991–2026), NYT 100 Best (2026 edition,
reconstructed from public press — NYT itself is paywalled). Raw source data
in `data/raw/recognition/*.json`; matching is name + normalized street
number, never name alone. Ambiguous cases go to
`data/processed/recognition_review.json` for human review, not auto-accepted.
James Beard data carries no addresses, so JB matches are exact-unique-name
only (confidence 0.9) — treat generic names with care. Re-match:
`python src/enrich_recognition.py` (raw files are cached; re-crawl only when
the guides update).

## Caveats

- `parse_quality` is honest: menu PDFs vary wildly (many are image-only);
  trust `menu_items` only where quality is `full`, use `raw_text` otherwise.
- Reservation links are constructed for OpenTable partners
  (`opentable.com/restref/client/?rid={partnerId}`); other partners get
  partner name/id only.
- Sunday participation is derived from "Sunday" meal-type labels.
- Week labels are stored verbatim (Week 1 disappears from the API's filter
  options once past; item-level `restaurantInclusionWeek` is what's stored).
