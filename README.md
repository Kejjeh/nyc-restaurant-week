# NYC dining — award roster + Restaurant Week tracker

**PUBLIC REPO as of 2026-08-02.** Site:
<https://kejjeh.github.io/nyc-restaurant-week/> (roster) ·
<https://kejjeh.github.io/nyc-restaurant-week/restaurant-week.html> (value dashboard)

This repo was private until 2026-08-02. It was opened after the owner spoke to
NYC Tourism, who said the only violation would be **hosting the exact PDFs** —
extracted text is fine. That conversation is the authority for this change; if
it is ever contradicted, revert to private and re-read "ToS rules" below.

**The one line that must never be crossed: do not host or mirror the menu PDFs.**
`data/raw/menus/*.pdf` is gitignored and 0 PDFs are tracked — keep it that way.
Menus are linked to the official S3 URL, never copied.

Local dataset + pipeline over **1,414 NYC restaurants**: every one named by
Michelin, the James Beard Foundation or the New York Times, plus all **636**
participants in NYC Restaurant Week Summer 2026 (Jul 20–Aug 16, extension weeks
through Sep 6; Saturdays excluded, Sundays optional per restaurant). Built to
re-run weekly. This README is the full context for operating the repo — no
session memory is assumed.

**The roster is the spine; Restaurant Week is a column on it.** That is a change
from how this repo started. The universe used to be "the restaurants in this
season's listing", and an award was a badge one of them might carry — which had
the relationship backwards. An award is the durable fact: the same restaurants
are recognised for decades. Participating in Restaurant Week is a marketing
decision one restaurant makes in one summer. So `venues` is the roster and
`venues.rw_slug` is nullable, and the site's front door
(`docs/index.html`) is the roster. The prix-fixe value dashboard still exists
in full, at `docs/restaurant-week.html`.

Season facts live in exactly one file, `config/season.json`. Nothing else in the
repo hard-codes a season, a year or a deadline. See "Season changeover" below.

## Sources & how discovery works

- Listing API: `POST https://program-api.nyctourism.com/restaurant-week`
  `{page, lookup}` → `{items(12/page), total, count, lookup}`. Auth = public
  `x-api-key` embedded in the site's JS; `config.discover_api_key()` re-extracts
  it automatically on 403 (cached in `data/cache/api_key.txt`).
- Addresses: scraped per restaurant from `nyctourism.com/restaurant-week/{slug}/`
  RSC payload (cached in `data/raw/details/`).
- Menu PDFs: public S3 bucket `nyc-tourism-public.s3.amazonaws.com/srw26/menus/`.
- Subway: MTA station locations from NY Open Data (`39hk-dx4f`, public domain).
- Outdoor dining: NYC DOT licensed setups from NY Open Data (`fpeh-f7ci`).
- Google ratings: Places API, keyed (never committed). Restaurants' own sites are
  crawled for prices (`price_sweep`) and menu terms (`menu_term_sweep`).
- Awards: `data/raw/recognition/` — the Michelin 2025 NYC selection (362, all with
  addresses), James Beard Foundation awards 1991–2026 (1,440 records, NYC only,
  **no addresses at all**), and the NYT Top 100 for 2026 plus one starred review.
  `config/awards.json` registers the three sources and prices every honour.
- Politeness: global ≤1 req/sec throttle (`config.throttle()`), browser UA.
  robots.txt allows everything used (`/*.json$` is disallowed; the pipeline
  deliberately avoids `_next/data` routes).

## How to run

```
pip install -r requirements.txt        # pdfplumber, playwright, pytest
python -m pytest -q tests/             # 289 tests, ~0.5s, no network
python src/refresh.py                  # weekly refresh + diff report
python src/refresh.py --force-menus    # also re-download PDFs (catches in-place edits)
```

`refresh.py` runs, in order: `fetch_listing` (dated snapshot →
`data/raw/listing/snapshot-YYYY-MM-DD.json`; per-page cache makes it resumable) →
`fetch_details` (new slugs only) → `download_menus` (skips cached) →
`parse_menus` (resumable) → `build_db` (rebuilds SQLite + CSV; reloads
`price_sweep` from cached sweep files) → `tag_dishes` → `enrich_recognition`
(re-matches from `data/raw/recognition/*.json`) → `fetch_outdoor_dining --refresh`
→ `fetch_google_ratings` (**only when `GOOGLE_PLACES_KEY` is in the env — it is
not in CI**) → `build_venues` → `resolve_venues` → `export_venues` →
`export_site_data` → `export_places` → `diff_report`.

`export_site_data` MUST stay after both `tag_dishes` and `enrich_recognition`:
`build_db` drops and recreates the DB, wiping the tag and recognition tables.
`export_places` MUST stay after `export_site_data`: it imports the exporter's
enrichment and shrinks its Google scores toward the *roster* mean.

`build_venues` MUST stay after `build_db` for the same reason `export_site_data`
does: it seeds the roster from the freshly rebuilt `restaurants` table, and
`build_db` drops the database first. `resolve_venues` with no flags only APPLIES
the committed Places cache — no key, no network — so CI runs it unchanged.

**Diff report** has two halves. The roster half answers *who got recognised, who
closed, and who arrived* — the questions that matter now that the roster is the
spine. A closure is the most booking-relevant fact this repo holds and nothing
else in the report would ever print it, because a restaurant closing is not a
listing change. It also tracks whether the unverified count is going down, so
778 unresolved rows do not quietly stay 778.

Last week's roster is read from `git show HEAD:docs/data/venues.json` rather than
from a sidecar history file. The menu-hash section keeps its own history and pays
for it with a documented wart — the report writes state, so a second run always
shows zero changes — and there is no need to repeat that: the previous payload is
already stored, versioned and immutable in HEAD. No previous payload (a first
run, a shallow checkout) is reported as such, not as an error.

It closes with **what a human owes the pipeline**: the count still waiting in
`recognition_review.json` and `venue_merge_review.json`, and which file to open.
Both had been quietly accumulating decisions nobody was ever told about, and a
refused merge is not a curiosity — it is an award that is *not on the roster*
until someone rules on it. Records that are already settled (applied spelling
folds, rulings written into `venue_aliases.json`) are excluded from the count,
because reporting them as waiting trains people to ignore the number.

The comparison itself is a pure function, `roster_changes()`, tested against
hand-built payloads. The listing half is not, and the price of that was a wrong
`parents[]` index that silently skipped the entire `SHORTLIST ALERTS` block on
every run for weeks while the report cheerfully printed everything else.

The listing half compares the two latest snapshots: a `SHORTLIST ALERTS` block
first (any change to `config/shortlist.json` restaurants' meal types, weeks, or
menu URL — booking-relevant), then program-wide added/dropped restaurants, field
changes, and menu-PDF content changes by sha256. If the roster was replaced in
*both* directions (more than half dropped and more than half added),
`season_boundary()` prints one line and suppresses the whole diff: at a
changeover every slug reads DROPPED and every shortlist alert is a lie.

**On a fresh clone**, `data/raw/menus/*.pdf` are absent (gitignored);
`download_menus.py` re-fetches all 473 at ≤1 req/sec (~10 min) on first run.
Everything else needed to rebuild the DB is committed.

### Tests

`python -m pytest -q tests/` — 289 tests, no network, run in CI *before* the
crawl so a broken guard fails in seconds instead of after ten minutes of polite
fetching. They cover the things that only bite at a season boundary and would
otherwise be discovered live: season config validation, the listing guards, the
export transition guards, the menu cache key, parsed-progress pruning, the diff
boundary, the seasons registry, Google matching, and places. `test_build_venues`
and `test_resolve_venues` cover the roster's merge rules, every one of them
written as a real pair from the award files rather than a made-up example.

On Windows pytest prints a `PermissionError: [WinError 5] ... pytest-current`
from an `atexit` callback *after* the run reports success. It is temp-dir
cleanup, not a test failure — the exit code is 0.

### Guards — the pipeline refuses bad data rather than publishing it

Every one of these exists because the failure it catches is silent: the run
succeeds, the payload is well-formed, and the site is wrong.

- `fetch_listing.validate_listing()` — refuses `total=0`; refuses a listing with
  fewer unique rows than `season.json` `min_rows` (400); keeps the pagination
  check (unique rows ≥ 90% of the API total); and **the season tripwire**: if no
  `menuFileUrl` in a 25-URL sample contains the season code, it raises and tells
  you to update `config/season.json`. A listing with no menu URLs at all only
  warns — early-season rosters pre-date the menu uploads.
- `export_site_data.assert_end_dates_in_window()` — every non-null `end_date`
  must land in `[book_by − 60 days, program_end]`. A date outside that window is
  what a stale season year looks like from the data side.
- `assert_not_shrunk()` — refuses to replace a published roster with one under
  80% of its size; `--allow-shrink` is the deliberate override. Measured against
  the season file, never against the legacy copy.
- `scoring_day()` — the rubric's window component counts down from
  `min(today, PROGRAM_END)`, so a post-season re-export cannot silently reshuffle
  every published grade.
- `test_no_hardcoded_season.py` — the README's own invariant, made testable.
  No shipped file in `src/` or `docs/` may QUOTE a season start, book-by, end or
  code; a date in a comment is prose about the value, a date in a string literal
  is the value. It has caught two real breaches after the fact and now catches
  them before. `LEGACY_SEASON = 'srw26'` is the one named exception — it points
  at a *past* season on purpose, to migrate localStorage keys written when there
  was only one, and a second test fails if that exemption ever stops describing
  something real.
- `assert_verified_gaps_reconcile()` — a hand-verified `comparable_usd` minus
  `rw_price` must equal `gap_usd`, and likewise for the high figures. These are
  printed as SOLID figures, the display state that means "checked against the
  restaurant's own printed materials", so a pair that does not subtract is worse
  there than anywhere else on the page. One entry breached it for weeks and
  nothing failed. An absent figure is not a contradiction — the decision doc
  often states a saving without stating a comparable.
- `verified_asof()` — pulls the transcription date out of
  `verified_values.json` `_doc.provenance` **by regex, not by word position**;
  rewording the sentence used to change a published date instead of failing.
- `assert_tos_clean()` — banned keys anywhere in the payload, plus snippet length
  and overlap over **both** `tags` and `offsite_tags` (the off-site snippets were
  never checked before).
- `enrich_recognition.check_norm_name()` — case tables asserting which names must
  collapse together and which must stay apart, run at the top of every execution.
- `download_menus` caches on **the URL that produced the file**, not its
  basename: S3 basenames repeat across seasons, so a returning restaurant's new
  menu would have been served from last summer's cache. A failed refetch keeps
  the good manifest entry (a fetch error used to render downstream as
  "image-only PDF"), and departed slugs are pruned instead of emitting orphan
  `menus` rows forever. `parse_menus` prunes parsed-progress to the manifest.
- `.github/workflows/refresh.yml` fails the run if a menu PDF would be staged or
  if `git ls-files` finds one tracked.

### Other tools

- `python src/hours_lookup.py <slug-or-name> [--refresh]` — on-demand opening
  hours for ONE restaurant (own-site JSON-LD, then text heuristics), cached in
  `data/cache/hours.json`. Deliberately never run in bulk.
- `python src/price_sweep.py --report` — ranked heuristic gaps from cached sweep.
- `node tools/verify_ui_counts.mjs` — drives a headless Chromium over both
  pages and checks that **every** facet chip, chart bar and roster facet value
  delivers exactly the number of rows it prints. Not in CI (no browser there);
  run it after touching `app.js`, `venues.js` or anything feeding a count into
  either. It runs the check twice: once on a clean page, and once with a filter
  already applied — which is the only pass that can catch a facet counted
  against the whole roster instead of against the rows surviving every *other*
  facet. Reintroducing that bug makes it print 16 mismatches, `Borough /
  Manhattan prints 1104, delivers 81` among them. Needs an existing Playwright:
  `PLAYWRIGHT_MODULE=... CHROMIUM_PATH=... node tools/verify_ui_counts.mjs`.
- `price_sweep.py --shard=i/k` / `price_rescue.py [--render] --shard=i/k` —
  one-off website price crawls (rescue's `--render` mode needs
  `playwright install chromium`). Results cache per slug in `data/raw/pricesweep/`.
- `python src/menu_term_sweep.py [--shard i/k] [--report]` — see "Off-site menu
  terms" below.
- `python src/fetch_google_ratings.py [--shard i/k] [--force] [--report]` — see
  "Google ratings".
- `python src/fetch_subway.py` — one-time MTA station pull; NOT re-run by
  `refresh.py`.
- `python src/fetch_outdoor_dining.py --refresh` — DOT licence register; IS
  re-run weekly, because licences are issued and expire continuously.
- `python src/places_cli.py add "Name" ["address hint"] | list | remove <slug>` —
  see "My list".

### Dish tags

`config/dish_tags.json` maps tag → regex rules with per-rule confidence.
**Adding a tag = one config entry + `python src/tag_dishes.py`** (no code edits).
Both structured `menu_items` and `menus.raw_text` are scanned so partial parses
are covered; every hit stores the matched snippet for auditing. Current
vocabulary: `braised`, `confit`, `raw tuna`, `snails` (`lumache` = low
confidence — usually the pasta shape; `wagyu toro` is excluded because it is
beef).

### Off-site menu terms

`config/offsite_tags.json` + `src/menu_term_sweep.py` are the same idea aimed at
a different document. `tag_dishes` reads the Restaurant Week prix-fixe PDF; some
dishes are essentially never on a $45–60 prix fixe — a seafood tower is an à la
carte raw-bar item — so searching RW menus for them returns nothing. These terms
are searched against **the restaurant's own website** instead (own domain plus
PDFs it links to; delivery aggregators are hard-blocked).

**A hit means "they serve it". It does NOT mean "it is in Restaurant Week."**
That distinction is load-bearing and has to survive into the UI: off-site tags
are a separate facet, never merged into the RW dish tags.

`config/offsite_verified.json` holds 56 hand checks (2026-08-05, read off each
restaurant's own posted menu) that beat the sweep **in both directions**:
`yes` (32) promotes the tag to confidence `verified` with the actual item and
price, `no` (24) removes it entirely. The removals matter more than the
promotions — the sweep can only see that a raw bar exists, and a raw bar is not
a tower.

### Google ratings

`src/fetch_google_ratings.py` → `data/raw/google/{slug}.json` (committed; the key
is not). One Text Search per uncached restaurant, so re-runs cost nothing and
only `--force` re-bills. **Matching is the hard part, not fetching**: a name is
not an identifier, and Text Search put "53" 621 m from the real restaurant. Every
result is checked against the coordinates and name already held, and a failure is
recorded as UNMATCHED with its reason rather than accepted.
`config/google_place_ids.json` holds 14 hand-pinned `place_id`s that skip the
geometry test — a pinned id *is* the verification.

The published `score` is a Bayesian shrinkage toward the corpus mean,
`(v·R + m·C)/(v + m)` with `m = 150`, because a 4.9 from 14 reviews is not
comparable to a 4.7 from 3,999. `m` was chosen against the actual distribution
(10th percentile = 188 reviews), so most restaurants stay essentially themselves
while the handful under 100 reviews get pulled meaningfully toward the middle.
The raw rating and the review count are both published alongside it — the
weighting exists to make sorting honest, not to hide the input.

### The rubric grade

`config/rubric.json` defines a transparent composite, published **with its parts**
so it can be argued with rather than taken on faith. Change the weights there and
re-run `src/export_site_data.py`; no code edits.

| component | weight | what it measures |
|---|---|---|
| `rating` | 30 | percentile of the weighted Google score |
| `award` | 30 | strongest distinction held, decayed by its age |
| `lex` | 20 | walk to the 4/5/6 — a **bonus above** a neutral 50, never a penalty: the ramp runs 50 → 100 from a 12-minute walk to a 2-minute one, so no line nearby and the longest walk score the same |
| `window` | 10 | days left to book — flexibility, not urgency; **inclusive** of the end date, so a window closing today is one day, not zero |

**The weights total 90, deliberately.** Each score divides by the weight actually
in play, so the numbers are read against each other; dropping a component raises
every other one's share proportionally rather than leaving a hole.

**`value` was removed after a sensitivity test**, and the disabled block is kept
in the config so restoring it is a one-line change. At weight 10 it moved
nothing: rank correlation 0.9915 without it, top 15 unchanged. Only 351 of 645
restaurants had a figure at all, just 14 of those were verified, and the
confidence multiplier compressed estimates into a 20–80 band. There was no weight
at which it was both meaningful and trustworthy. Value is not lost from the site —
the gap column, both gap sorts, the best-value sort and the verified-gaps preset
all show it honestly instead of burying a heuristic inside a composite. **The
grade answers WHICH DINNER, not which deal**; Restaurant Week already fixes price.

**Missing components are IMPUTED at that component's own mean**, never zeroed and
never dropped. A 0 is published only when the zero is a *fact* (holds no award).
Two earlier versions got this wrong in opposite directions: redistributing the
weight rewarded thin data, and shrinking the total toward the overall mean
punished it. Every imputation is disclosed — `rubric_parts` marks it and
`rubric_completeness` reports the share of the score that rested on real data.

## The venue roster (`venues`, `venue_awards`)

`src/build_venues.py` builds the roster: one row per real restaurant, whether or
not it is in Restaurant Week, whether or not it is still open. It seeds from the
`restaurants` table — those rows are the only ones that arrive with verified
coordinates and a neighborhood — then folds in the three award files, addresses
first (Michelin, then NYT, then James Beard).

**Merging is the whole problem, and the two failure modes are not symmetrical.**
A wrong merge silently turns two restaurants into one row, and nobody notices
until they book the wrong one. A missed merge shows the same restaurant twice,
which is ugly and obvious. The thresholds are set against the invisible one, and
anything the rules will not decide is written to
`data/processed/venue_merge_review.json` rather than guessed.

The rules, strongest evidence first:

| Situation | Rule |
| --- | --- |
| Source has an address, street number agrees | merge, confidence 1.0 |
| Source has an address, our row has none | merge and adopt it — an absent address is not a contradiction |
| Same name, same ZIP, different street number | merge, but logged under `confirm` for a human. This is the Ci Siamo case: Manhattan West publishes it as both 385 Ninth Ave. and 440 W. 33rd St. |
| Same name, different building | a **new** venue — Fish Cheeks and Tonchin genuinely have two |
| Source has no address (all 1,363 Beard records) | merge only onto a name that is unique in the roster; two candidates is a review row, not a coin flip |

Two more passes run after every source is in, because both need the whole roster:

**Spelling variants.** `Momfuku Ssam Bar` and `Momofuku Ssam Bar` are one
restaurant; `La Pecora Bianca Upper East Side` and `...Upper West Side` are two.
A similarity ratio cannot tell them apart — it scores the pair that must stay
apart (0.969) *higher* than the pair that must fold (0.963). What separates them
is which token differs and whether it is a spelling of the other or a word chosen
to contrast with it. So exactly one token may differ on each side, those two
tokens must share a first letter and score ≥ 0.85, **and the names must share at
least one other token**: `isa`/`insa`, `mam`/`mamo` and `sevilla`/`semilla` are
all one edit apart and all different restaurants, and what they have in common is
that the differing token is the entire name. The surviving row is chosen for its
data; the surviving *spelling* is whichever the sources use more often, which is
why Uncle Boons is no longer displayed as "Uncle Boon".

**Portfolio awards.** Outstanding Restaurateur is recorded as one string listing
every room the winner runs — `"Frenchette, Le Veau d' Or, and Le Rock"` — and
left alone it becomes a venue with that name while the actual restaurants never
receive the award. Splitting is easy; the hard part is that Gage & Tollner, Milk
& Honey and Grand Central Oyster Bar and Restaurant are single names with the
same punctuation.

Two questions, answered separately, because conflating them made the roster
depend on the season:

*Is this string a list?* A group marker settles it — a parenthesised list, "and
others", or two or more commas, none of which occur in a real name. Otherwise
**two of its parts must resolve to venues that hold an award of their own.** Not
merely to venues: a venue that exists only because it joined this summer's
Restaurant Week is gone next summer, and gating on those meant the answer
changed with the listing.

*Which parts are restaurants?* Once the string is a confirmed list, **all of
them**, unless the part's own shape says otherwise — under three characters, a
bare `LA`-style abbreviation, or containing a slash, which means the source
packed two things into one part and guessing which half is the restaurant is how
a venue called `NY/Matsuhisa` happens. This is a test on the string, never on
whether we have heard of the name: whether the Beard Foundation named Morandi in
a 2010 award is a fact about the award.

That second rule is what puts Pastis, Pravda, Lucky Strike, Reynard, Undercote,
Cafe Zaffri, Etérea, The Breslin, Tosca Cafe and Le Veau d'Or on the roster at
all. Every one is a real restaurant that appears in these files *only* inside a
portfolio string, and every one used to be dropped.

The property this buys is worth stating: **no award on the roster depends on who
joined Restaurant Week this summer.** A simulated changeover — 516 of the 636
participants replaced — loses zero award records. It used to lose twelve.

### When the rules will not decide (`config/venue_aliases.json`)

The rules above are deliberately conservative, so a few award-record names are
left over that a person can settle in a second and a threshold cannot settle
safely at all. Those rulings live in `config/venue_aliases.json`, in the same
spirit as `config/recognition_suppress.json`:

- **`not_venues`** — names that are people, companies or publications rather
  than restaurants (`Dale DeGroff Co., Inc.`, `Founders, "Food & Wine" and
  "Food Arts"`, and the corporate names of six restaurant groups). The record is
  dropped with its reason recorded. No venue is created, and nothing is quietly
  attached to some other restaurant instead.
- **`split_into`** — strings that genuinely are a list, where the automatic
  split cannot prove it because fewer than two parts were already on the roster.
  `Zaab Zaab, Zaab Zaab Talay` is two real Queens restaurants and neither
  appeared under its own name anywhere in the award files. A human ruling lets
  the parts be *created* rather than required.

Loosening the automatic rules instead was the wrong trade. Every one of these is
one edit away from a rule that would also mis-handle a real name: anything that
splits `Zaab Zaab, Zaab Zaab Talay` also splits `Fifty Seven Fifty Seven, The
Four Seasons Hotel` and `Lorenzo's Restaurant, Bar & Cabaret`, and those failures
are the invisible kind. Nine hand rulings cost nothing and break nothing.

### Standing (`prestige`)

A 0–100 composite, defined entirely in `config/awards.json` — change a weight
and re-run `build_venues.py`; there is nothing to edit in Python. Base is the
single best honour held, plus a linear bonus for NYT rank (No. 1 earns the most),
plus a breadth bonus for each independent jury beyond the first, multiplied by a
recency factor keyed on the most recent honour, multiplied last by a closed
penalty. Unlike `rubric.json` there is **no imputation**: an award is a fact or it
is absent, and absence is not an average.

Recency keys on the venue's most recent honour of any kind, not on each award's
own age — a 1995 Beard winner that took a 2025 Michelin star is a current
restaurant, and is scored as one. The year it measures from comes from
`config/season.json`, because that is the only file in this repo allowed to
carry a year; `awards.json` can pin an explicit one to reproduce a past scoring
run, and a non-integer there fails the run rather than scoring against garbage.

### Resolving venues against Google (`src/resolve_venues.py`)

769 venues arrive with no coordinates and no confirmed open/closed status,
because the Beard file carries no addresses. `resolve_venues.py --fetch` looks
them up — roughly 700–800 Text Search calls, billed once and then cached in
`data/raw/venues_google/` exactly like `data/raw/google/`.

```
python src/resolve_venues.py --report            # what is still unresolved
python src/resolve_venues.py --dry-run           # the exact queries, and the cost
python src/resolve_venues.py --fetch --limit 50  # needs GOOGLE_PLACES_KEY
python src/resolve_venues.py                     # apply the cache; no key, no network
```

`--dry-run` prints the Text Search string every venue would be sent, marks the
ones with no address on our side (`!` — only the name and the NYC bounds can
confirm those), and estimates the bill. It sends nothing and needs no key. The
run it previews costs real money and cannot be undone, so the person paying
should be able to read the queries first.

Both paths build the query through `query_for()` rather than each rolling its
own, and a test enforces that: a dry run that constructs its own string is worse
than no dry run, because it invites confidence in something never tested. That
is not hypothetical here — the dry run's first execution revealed that every
address-carrying venue was being queried as `"… New York, NY 10022 New York"`,
with the city stapled on twice. Fixed before a cent was spent.

`fetch_google_ratings.py` can judge a candidate on **distance**, because every
Restaurant Week row has coordinates. Nothing here does, so this file's rule is
weaker and refuses far more readily: the result must land inside the five
boroughs, and either an address we already hold must agree (street number, or ZIP
for a second entrance) or the name must carry it alone at ≥ 0.62 similarity.
Everything else is left unresolved *with its reason recorded*. An unresolved
venue is a visible gap someone can fix; a wrongly resolved one silently attaches
a rating, a location and an "open" badge to the wrong restaurant.

**`unknown` status is not a claim that a restaurant closed.** It means nothing has
confirmed either way, and the site labels it "Unverified" and says so on hover.
Google returning `ZERO_RESULTS` is suggestive for a 1994 award and meaningless
for a 2026 one, so it is recorded, not acted on.

Closed venues stay on the roster with their awards intact — they were earned —
and the closed penalty keeps them below anywhere you can actually book.

## Season changeover (winter 2027)

Ordered. This is the section that will actually be used in January.

1. **Edit `config/season.json` — and nothing else.** `code`, `label`, `year`,
   `start`, `book_by`, `end`, `min_rows`. `src/config.py` loads and validates it
   at import (code must match `srwNN`; the three dates must be ISO; `book_by`
   must not be after `end`). **Verify `code` against a live `menuFileUrl` value —
   never assume the next prefix.** If you get it wrong the crawl fails loudly:
   the season tripwire in `validate_listing()` refuses a listing whose menu URLs
   don't carry the code.
2. **Rotate `data/raw/listing/` snapshots.** Cross-season diffs are meaningless.
   `diff_report` will detect the boundary and suppress the diff for one run
   anyway, but stale snapshots keep polluting it afterwards.
3. **Reset `config/shortlist.json` and `config/verified_values.json`.** Both are
   season-specific by nature: last season's booking targets and last season's
   hand-transcribed windows are not facts about the new roster.
   `verified_values.json` `_doc.provenance` must carry a fresh ISO date or the
   export refuses to run.
4. **Menus need no manual step.** `download_menus` keys its cache on the URL, so a
   returning restaurant's new menu is fetched rather than served from the old
   one; the manifest and parsed-progress self-prune to the live roster.
5. **Re-enable the cron** in `.github/workflows/refresh.yml` if it has been
   paused by then (see "Automation" below).
6. **Google ratings for new restaurants.** `refresh.py` skips
   `fetch_google_ratings.py` unless `GOOGLE_PLACES_KEY` is in the env, and the
   workflow does not currently pass one. So either add a `GOOGLE_PLACES_KEY`
   Actions secret and wire it into the workflow env, or run
   `python src/fetch_google_ratings.py` locally and commit the resulting
   `data/raw/google/*.json`. **Skipping this is not neutral**: an unrated
   restaurant has its `rating` component — 30 of the 90 available grade points —
   imputed at the corpus mean.
7. **The roster needs nothing, and that is worth knowing rather than assuming.**
   `build_venues.py` re-seeds from the new season's `restaurants` table, so last
   season's participants that hold no award simply stop being venues — correct,
   since the roster is *award venues plus current participants*. Award venues
   persist untouched; `data/raw/venues_google/` is keyed by venue slug and stays
   valid; `awards.json` no longer carries a year, so recency follows
   `season.json` with the rest. The one thing to expect is a noisy `## ROSTER`
   block for one run: several hundred venues gone and several hundred new, which
   is the changeover, not a fault. It is capped and counted, not silently
   truncated.
8. **Archives and saved state need nothing.** The exporter writes
   `docs/data/seasons/<code>.json` and merges one entry into
   `docs/data/seasons.json`, copying every other season through untouched; the
   frontend namespaces `localStorage` per season code.
9. Expect new week labels in `restaurantInclusionWeek`. The API URL slug
   (`restaurant-week`) is unchanged across seasons, and the API key survives
   seasons and self-rediscovers if rotated.

## Automation

`.github/workflows/refresh.yml` — **the Monday 07:00 ET cron is currently ACTIVE**
(`cron: '0 11 * * 1'`; GitHub cron is UTC-only and ignores DST, and the season
ends well inside EDT). Plus `workflow_dispatch` with a `force_menus` input.
Concurrency group `rw-refresh` with `cancel-in-progress: false`, so two refreshes
can never crawl nyctourism at once and blow the ≤1 req/sec limit.

The job restores a PDF-only cache (`data/raw/menus/*.pdf` — never the whole
directory, which would restore stale copies over the three tracked JSON files),
runs the tests, runs `refresh.py`, builds a job summary that leads with the
shortlist alerts, runs the PDF guard, and commits with rebase-and-retry.

**Planned, NOT YET DONE:** final refresh around **Sep 5–6**, after which the
`schedule:` block gets commented out and only `workflow_dispatch` remains.
Two reasons: GitHub silently auto-disables scheduled workflows after 60 days of
repo inactivity, and an explicit pause with a comment is self-documenting where a
silent disable is not. Re-enabling it is step 5 of the changeover.

### The Monday notification (`src/job_summary.py`)

The log is tens of thousands of characters and nobody reads it. The **summary**
is what arrives, so what gets promoted out of the fold is the whole question.

Two things lead it, because they are the two that change what somebody should
*do*: a **closure**, and a **shortlist restaurant's booking details moving**. A
closure is the only fact in the whole report that is not recoverable from
anywhere else — it is not a listing change, so nothing else would ever mention
it — and until this existed it landed inside a collapsed `<details>` blob in a
50,000-character dump. It is named once above the fold; the raw copy stays in
the fold, which is meant to be complete.

The logic is a module rather than inline YAML so it can be run and tested
without pushing a commit and waiting for Monday. Inline is precisely how a wrong
`parents[]` index came to silently skip the entire `SHORTLIST ALERTS` block on
every run for weeks while the report printed everything else.

### Checks on every pull request (`.github/workflows/checks.yml`)

The tests used to run in exactly one place: inside the Monday refresh, before
the crawl. That is the right place for them — a broken guard should fail in
seconds rather than after ten minutes of polite fetching — but it meant a pull
request got no CI at all, and a break introduced on a Tuesday was found by the
cron the following Monday.

`checks.yml` is the fast half, on `pull_request` and on pushes to `main`. It
never crawls, never fetches and never commits:

1. the 289 tests
2. **no menu PDFs are tracked** — the same guard the refresh runs before it
   commits, moved to before a branch can be merged
3. **both payloads still validate** — `export_site_data.py --check` and
   `export_venues.py --check`, which write nothing and exit non-zero on any error
4. **the committed roster payload is up to date** — rebuilds and re-exports, then
   compares field by field with `generated_at` dropped. That field is the wall
   clock and changes on every export, so a plain `git diff` would fail this step
   100% of the time and teach everyone to ignore it.

Guard 4 works because `export_venues.py` skips the write entirely when nothing
but the clock moved, which also keeps a 1.2 MB payload out of fifty-one of every
fifty-two weekly commits.

## Database schema (`data/processed/restaurant_week.sqlite`)

**restaurants** (636 rows) — one per participant
- `slug` PK · `name` · `borough` · `neighborhood` · `address` (NULL for 2, see caveats) · `lat`/`lng`
- `cuisines` JSON array (listing tags minus neighborhood)
- `price_tiers` JSON array, e.g. `["$30","$45"]`
- `meal_periods` JSON array of lunch/dinner/brunch (derived)
- `meal_types_raw` JSON array, verbatim API labels (e.g. `"$60 Sunday Dinner Price"`) — the ground truth `meal_periods`/`sunday_participation` derive from
- `weeks` JSON array, verbatim week labels; last element ⇒ end week
- `sunday_participation` 0/1 (derived: any "Sunday" meal type)
- `menu_url` — **the EMPTY STRING `''` (never NULL) when the restaurant publishes
  no RW menu PDF — 175 rows.** `WHERE menu_url IS NULL` matches ZERO rows; use
  `menu_url = ''`, or branch on whether a `menus` row exists. · `website`
- `reservation_partner`/`reservation_partner_id`/`reservation_link` (link built for OpenTable only)
- `listing_url` · `summary` · `collections` JSON · `snapshot_date`

**menus** (473 rows) — one per downloaded RW menu PDF. **No row for a restaurant
= no menu published (175).** 12 rows currently belong to slugs that left the
program on the Aug 10 snapshot; the prune landed 2026-08-14 and clears them on
the next refresh.
- `restaurant_slug` FK · `menu_url` · `pdf_file` · `sha256`
- `parse_quality`: `full` (≥2 course sections + ≥4 dishes extracted — trust
  `menu_items`, 240 rows) / `partial` (text extracted, structure unclear — use
  `raw_text`, 178) / `failed` (PDF exists but is image-only or corrupt — 55 rows;
  OCR deferred)
- `raw_text` — full extracted text. **Never publish this** (ToS).

**menu_items** (9,261) — structured dishes from `full` parses
- `menu_id` FK · `course` · `dish` · `description` · `supplement_price` · `position`

**menu_item_tags** (440) — dish-tag hits across 163 restaurants
- `restaurant_slug` · `menu_id` · `menu_item_id` (NULL = hit came from raw_text)
- `tag` · `confidence` high/low · `matched_text` (snippet, audit + display-safe
  short quote) · `source` item (137) / raw_text (303)

**recognition** (184 rows, 77 restaurants) — external recognition matched to
participants
- `restaurant_slug` · `source` michelin/james_beard/nyt · `level` — only **7**
  strings actually occur: michelin `1 star`(6)/`bib_gourmand`(13)/
  `recommended`(38, = Michelin's "The Plate"); james_beard `winner`(22)/
  `nominee`(54)/`semifinalist`(46); nyt `nyt_100_best`(5). `2 stars` and
  `nyt_starred_review` exist in the RAW files but no such restaurant
  participates — don't assume they render, don't crash if a future snapshot
  adds them.
- `year` · `source_url` · `matched_name` · `notes`
- `match_confidence`: 1.0 = exact name + street-number match (55 rows); 0.9 =
  exact unique name, no address available (129 rows — ALL james_beard, since JBF
  publishes no addresses); 0.8 = fuzzy name ≥0.92 + address (defined in code, 0
  rows currently). Below 0.8 is never inserted — those go to
  `data/processed/recognition_review.json` (see caveats).
- Raw source data: `data/raw/recognition/*.json` (committed — not regenerable by
  the pipeline; JB award names live only there).

**price_sweep** (600) — automated website price triage
- `restaurant_slug` · `comparable_3course` (median-stack estimate, $, present on
  370 rows) · `gaps` JSON `{"$45": 14, ...}` (comparable minus RW tier) ·
  `confidence` high(289)/medium(66)/low(182)/none(63) · `pages_fetched` ·
  `n_prices` · `error` · `swept_date`
- `comparable_basis` = `'heuristic'` on every row: **triage-grade, never
  verified**. Known biases: understates expensive kitchens (mains capped at $70),
  overstates casual formats. Hand-verified findings live in `reports/`, not here.


### `venues` — the roster (1,414 rows)

```sql
venue_slug TEXT PRIMARY KEY      -- equals rw_slug where the restaurant is in the programme
name, address, lat, lng, borough, neighborhood
rw_slug    TEXT REFERENCES restaurants(slug)   -- NULL = not in Restaurant Week
status     TEXT   -- 'open' | 'closed' | 'unknown'; unknown means UNCONFIRMED, not closed
status_source TEXT              -- what established it; never a guess
place_id, rating, user_ratings_total
first_award_year, last_award_year, award_sources (JSON), award_count
top_honor, top_honor_label      -- the best single honour held. NOT the set it
                                -- holds: a Beard win scores above one Michelin
                                -- star, so Daniel's best honour is the Beard
                                -- one. The roster's "Honours held (any)" facet
                                -- is derived from venue_awards for that reason,
                                -- and every preset named after an honour uses it.
prestige   INTEGER              -- 0-100, per config/awards.json
seeded_from, resolution         -- which source created the row, and how identity was settled
```

### `venue_awards` — one row per award record (1,941 rows)

```sql
venue_slug, source, level, award, year
rank      INTEGER   -- NYT Top 100 position, parsed out of the notes field
person    TEXT      -- Beard awards are frequently to a CHEF, not to a room;
                    -- hiding that would have the roster claim a restaurant won
                    -- things its chef did
source_url, matched_name, match_confidence, how
```

`recognition` (the older, Restaurant-Week-scoped table) is untouched and still
feeds the dashboard's rubric. `venue_awards` is the superset: it keeps the 1,000+
award records belonging to restaurants that have never been in the programme,
which `recognition` drops on the floor by design.

## Reports (`reports/`)

- `rw-final-bookings.md` — **the decision doc**: 15 ranked bookings with
  own-site-verified windows, gaps, booking links, BOOK-BY flags. Supersedes the
  value report for decisions. The 2026-08-14 addendum records the Aug 10 mass
  extension (Aug-31 enders collapsed 267 → 32 while Sep-6 enders grew 163 → 401,
  roster 645 → 636) and the expiry status of each of the 15.
- `rw-summer-2026-value-report.md` — archive: full value analysis, traps,
  evidence grades ([A] own menu … [D] uncorroborated), 3 addenda, source tables.

## The site (`docs/`, served by GitHub Pages)

Two pages, one design system. `styles.css` owns the palette, the masthead and the
controls; both pages read it, and the theme toggle writes the same `rw-theme`
key, so a viewer's choice survives the hop between them.

- **`index.html` + `venues.js` + `venues.css` — the roster.** The front door.
  All 1,414 venues, searchable and filterable by highest honour, honours held,
  jury, borough, trading status, Restaurant Week participation and cuisine;
  sortable by standing, recency, award count, weighted rating, name or how long
  the restaurant has been recognised.

  **Weighted rating** is the same shrinkage the dashboard and `places.json` use
  — `src/config.py:GOOGLE_PRIOR`, published in the payload beside the mean it
  shrinks toward, so the page never spells either number out. The ratings come
  from the participants' own `data/raw/google/` cache, folded in by
  `resolve_venues.apply_participant_cache()`: those 629 rows never needed a
  Places lookup of their own, so nothing had been reading their records into
  `venues`, and the sort had nothing to sort on. Each row expands to every award record it
  holds, with the year, the chef where the award was to a person, and a link to
  the awarding body.

  The design problem here is **absence**. A dashboard row has a price, a menu, a
  gap and a subway walk; most roster rows have a name, some awards and nothing
  else. So elements do not render at all when their field is missing, rather
  than printing a dash or a zero that looks like a measurement. Honour colour is
  by *jury*, not by rank, and never carries meaning alone — the badge always
  spells the honour out, so the page works in greyscale. Trading state is
  carried by shape as much as colour: open is quiet, closed is struck through,
  unverified is dashed, matching the grammar the dashboard already uses for an
  estimated price.

  **Dish tags** are the only thing on the roster that answers *what does this
  place actually cook*. The cuisine facet cannot: cuisines come from the
  Restaurant Week listing, so the 778 venues that were never in the programme
  have none. Tags come from the parsed menus — `game meats`, `foie gras`,
  `snails`, `sweetbreads` — and are searchable, filterable, and printed on the
  row. There is a `Game, offal & odd cuts` preset because that is the question
  the roster was first asked; it returns 38 restaurants, not the 44 it returned
  before the confidence split, because six of those rested on a weak match.

  Tags are split by confidence, the same way the dashboard separates a verified
  gap from an estimated one. A tag is the **confident** claim when any of its
  matches on that menu was high; `dishes_maybe` holds the rest and the row marks
  them with a dashed underline and a `?`. 50 of the 64 low-only pairs are
  `truffle` — truffle honey, truffle mayo, truffle sour cream — where the word
  is on the menu but the dish is not about it. Seven are `snails`, where the
  same softness matters much more to somebody filtering for escargot. **Filters
  count only the confident claim; search matches both.**

  The payload carries the tag **name** only, never the snippet `restaurants.json`
  carries with each one. A name is a derived fact and holds none of the menu, so
  this file stays free of menu text entirely rather than living inside the
  5%-of-a-menu budget the dashboard has to manage. A test asserts every
  published name is a configured tag and that nothing longer than a tag name
  gets through.

  Two links per row, where the data supports them. A Restaurant Week badge is a
  deep link — `restaurant-week.html#r=<slug>`, which the dashboard reads and
  `openRestaurant()` honours by clearing whatever was filtered, so the link
  wins. Landing someone on a 636-row list and leaving them to find the name
  again is a hint, not a link. A **Book** link goes to the restaurant's
  reservation partner or its own site. Only the 636 Restaurant Week rows carry
  one — the award files hold no websites — so it is absent on the rest rather
  than a dead control that looks identical for everyone.

  Facet counts are computed against the rows surviving every **other** filter —
  never the facet's own. A count is a promise about what clicking it will give
  you, and the roster used to count against the whole roster: filtered to the 62
  Michelin-starred venues it still read `Manhattan 1104` and `Queens 55`, and
  clicking Queens returned nothing. Excluding a facet from its own basis is what
  keeps a second choice in the same group reachable (Brooklyn *or* Queens); a
  ticked value stays on screen at zero so it can be unticked. Same rule the
  dashboard states in its own source, now stated the same way here.

  Filter groups list the 14 commonest values and then say how many they are
  hiding (`+42 more — use the search box`). A cut list that does not admit it
  was cut reads as "those are all of them", and someone looking for Georgian
  would conclude the roster has none. A ticked value is kept on screen wherever
  it sorts, so searching cannot make your own tick disappear.

  It loads no map and no third-party anything, so its CSP is tighter than the
  dashboard's: no unpkg, no CARTO.

- **`restaurant-week.html` + `app.js` — the value dashboard**, unchanged. Prices,
  menus, gaps, the rubric, the map, the planner. It is the prix-fixe drilldown
  for the 636 restaurants in the current season, and it is linked from the
  roster's masthead and footer (and back).

## Dashboard details (`app.js`)

Static sort/filter dashboard over the 636 participants. No backend, no build
step: `docs/index.html` + `docs/app.js` + `docs/styles.css`. Preview locally with
`python -m http.server 8137 --directory docs` (a `file://` open will NOT work —
the payload is fetched).

**Assets are versioned** — `index.html` loads `app.js?v=N` and `styles.css?v=N`.
**Bump the number whenever you change either file. This is commit discipline,
not a nicety.** A browser holding a cached `index.html` against a fresh `app.js` was
crashing `apply()` partway through, leaving the rows rendered but silently
costing the filter panel, the quick views and the view switcher. `$opt()` is the
other half of that fix: elements a stale `index.html` may not have degrade to a
throwaway node instead of throwing.

`?today=YYYY-MM-DD` freezes "today" for debugging phases and countdowns. The page
is `noindex` and personal; this is a debug affordance, not a feature.

### Seasons

The payload lives at `docs/data/seasons/<code>.json`, indexed by
`docs/data/seasons.json`. The frontend loads the registry, then picks the season
named by `#season=`, else the `live` one, else the newest by end date; with more
than one season the masthead label becomes a switcher. A run speaks only for its
own code — every other registry entry is copied through untouched, so a winter
build cannot rewrite the summer archive.

`docs/data/restaurants.json` is still written as a **TEMPORARY** fallback for
pages holding a cached `index.html` that boots the old way. It goes when it is
provably unread.

Saved lists and plans are namespaced `rw-saved:<code>` / `rw-plan:<code>`, with a
one-time migration of the legacy unnamespaced keys into `srw26`. Without this,
switching seasons showed phantom saves for slugs the other roster never had.

### Season phase

`seasonPhase()` returns `core` (through `book_by`), `extensions`, or `archive`
(past `program_end`, or the registry says archived — a stale `archived` stamp
cannot be wrong in that direction, since a season never un-ends).

In `archive` the "still bookable today" date filter is **not applied**. It used to
be, which meant that after Sep 6 every window had passed, all 636 rows were
filtered out, and the page blamed the reader's filters for the season ending. The
archive shows the whole season with a banner naming the run's start and end.
`Closing soon` disappears, the stats countdown states the end date instead of
counting, its companion tile counts what DID end rather than a truthful-but-
useless zero, and Plan reads as a record of the season.

Urgency is a **live 7-day window from today** (clamped to `program_end`), not
frozen at `book_by` — frozen, every closed restaurant kept shouting BOOK BY after
the date passed.

### Navigating it

The default sort is **Best value — verified first**: ranked picks, then
hand-verified gaps, then estimates, with gap size ordering rows *within* each
tier. The raw `Gap $ — high to low` sort is still there as an explicit choice,
and the estimate caveat banner appears only on that one, because that is the
sort where heuristics lead. Rationale: a verified $18 is a better thing to show
first than a heuristic $79 computed from a scraped price list at a restaurant's
cheapest tier. Other sorts include rubric grade, Google weighted, Google raw
stars, distinction, subway walk and final-list rank.

**Quick views** (★ Saved / My list / The 15 / Closing soon / Verified gaps /
4/5/6 / Sunday / Michelin / Top awards) each set filters *and* sort in one tap
and are toggles — tapping the active one returns you to everything. `Michelin`
names all three Michelin rungs because the recognition facet holds tiers now;
`Top awards` exists because a bare Michelin chip is mostly The Plate. Applied
filters show as removable chips, so you never have to open the panel to see
what's on. **★ Save** (in each row's detail) keeps your own shortlist in
`localStorage`, separate from the curated ranking; the Saved view only appears
once you've saved something.

Only the search box, Filters and Sort are sticky — pinning the quick views and
counts too left 43% of a phone screen for content. Rows render 50 at a time
(auto-loading on scroll, `Show more` as the keyboard path), keeping the page to
roughly a tenth of the DOM a full render would build. Sorting by end date groups
rows under closing-date headings. The filter panel has a **find-a-filter** box
that searches all groups at once, and long facets (77 neighborhoods, 56 cuisines)
collapse to the 12 most populated with a `Show N more`. `/` focuses search,
`Esc` clears it or closes the panel. Filter state lives in the URL hash, so any
view can be bookmarked.

**Five views.** `List` · `Map` · `Stats` (headline tiles + four click-to-filter
SVG charts — closing dates, gap basis, borough, cuisine — no charting library,
describing the whole programme) · `Plan` · `Compare`.

**Compare** (a tab that appears once you have saved two or more, capped at 6
columns) puts your saved restaurants side by side — attributes down the side,
restaurants across, with a dot on the better value in rows that have one. Ties
share the dot, and a
row where everything ties gets no dot at all. The `Book` row names the platform
you are about to land on (OpenTable / Resy / SevenRooms / Tock). **251 of the 636
publish no booking link at all**; those get a quieter `Restaurant site` button
labelled "no booking link" rather than being hidden — a link to their own site is
useful, but it is not a reservation and must not be dressed as one.

**Plan** turns the ★ shortlist into dated bookings. Each restaurant's date
picker offers ONLY days it can actually serve you: inside its window, never a
Saturday unless it carries the `saturday_service` flag (4 do), and a Sunday only
where Sunday service is established (so Mark's Off Madison offers none despite
the API's Sunday claim). Assignments live in `localStorage`. **A date that has
passed is no longer deleted** — it renders as `went here — <date>`, because a
plan you executed is a record, not an error. Anything else that has become
impossible says why.

**Subway proximity.** `python src/fetch_subway.py` pulls MTA station locations
once from NY Open Data (public domain, dataset `39hk-dx4f`) into
`data/raw/subway/stations.json` — committed, and deliberately NOT re-fetched by
`refresh.py`. The exporter attaches, per restaurant, every route within a
12-minute walk plus the nearest station (616 of 636 have one). Drives a
`Subway line` facet, a `4/5/6` quick view (336 restaurants), and a `Subway walk`
sort that measures to whichever lines you've selected. Distances are
straight-line × 1.3 at 80 m/min — an approximation, labelled `~` in the UI, not a
routing engine.

**Outdoor dining.** `src/fetch_outdoor_dining.py` pulls the DOT licence register
(dataset `fpeh-f7ci`) and the exporter matches it by name + house number +
distance: 137 restaurants are in the register (sidewalk and/or roadway), 20 more
are only *described* as having outdoor seating in their marketing copy, and 479
are unknown. The register is the only defensible basis for the filter — the
listing API carries no such field, and inferring from the marketing summaries
would mostly be inferring from whether a copywriter felt like mentioning a patio.

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

### My list (restaurants that are not in Restaurant Week)

Half of what you want a where-to-eat list for is restaurants no programme covers.
`config/places.json` holds them (add by hand, or
`python src/places_cli.py add "Le Bernardin"` which resolves the name through the
same Text Search machinery and asks for a y/n before writing — there is no
coordinate of our own to corroborate against, so the human confirmation IS the
verification). `src/export_places.py` writes `docs/data/places.json`.

Rows arrive in the **same shape** a participant does, so the frontend needs no
second vocabulary, and carry `source: "mine"`. They get the enrichment that is
not programme-specific — coordinates, the subway walk, and the same Bayesian
score shrunk toward the same roster mean, **imported from the exporter rather
than reimplemented** so a place and a participant are never scored two ways.
Every Restaurant Week field is `null`: no price, no gap, no window, no rubric,
no rank. Inventing any of it would be a lie about the programme. They filter as
`My list`, sort last where they have no figure, never touch the stats, and in
Plan they carry no window, so the note stops promising a Saturday rule that does
not apply to them. The ToS guard runs over this payload too.

### The payload

`src/export_site_data.py` builds it (`--check` validates and prints without
writing). It merges, in this precedence order:

1. **`config/verified_values.json`** — hand-verified facts transcribed from
   `reports/rw-final-bookings.md` and the caveat list below. The restaurant's
   own printed materials always win over the listing API. **47 entries.**
2. **listing API fields** from `restaurants` (end date parsed from the last
   `weeks` label, with the year taken from `config/season.json` — the label
   carries a month and a day but never a year).
3. **`price_sweep`** — heuristic only, always rendered as an "estimate", never
   allowed to populate a verified field.

Top-level keys: `generated_at` · `season_label` · `season_code` · `season_start` ·
`snapshot_date` · `verified_asof` · `book_by` · `program_end` · `tag_vocabulary` ·
`rubric_weights` · `rubric_component_means` · `rubric_mean` · `google_mean` ·
`google_prior` · `restaurants`.

Per-row, beyond the obvious identity/price/window fields: `google`
(`{rating, reviews, score, place_id, matched_name, basis, closed}` — `basis`
`place_id` means a human pinned it, `textsearch` means it was accepted on
coordinates, and the UI must not overstate the difference) · `rubric`,
`rubric_parts`, `rubric_completeness`, `rubric_imputed` · `offsite_tags` ·
`outdoor` (`{sidewalk, roadway, licensed, described, licence_name, dist_m}`) ·
`recog_top`, `recog_rank`, `recog_eras` · `verdict` / `verdict_note` ·
`end_date_source` / `end_date_api` and `sunday_source` / `sunday_api`, which keep
the API's claim next to the verified one rather than overwriting it · `flags` ·
`grade` · `subway` / `subway_nearest`.

`config/recognition_suppress.json` drops recognition rows that are in the DB but
demonstrably wrong (see caveats). Set `active: false` to restore them.

**Issue links in a pull request body are load-bearing.** GitHub closes an issue
on merge when a closing keyword sits next to its number, and it does not read
negations — a PR body containing *"This does not close #2"* closed issue #2, and
the issue was then attributed to a pull request whose own text says it did not do
the work. Say `Closes #2` to close one and `issue 2` to mention one;
`.github/pull_request_template.md` carries the rule where it will be read.

**The Monday summary says when the refresh did not finish.** It renders three
named sections of the log, and when the chain died before any of them existed
it rendered a masthead and `## Shortlist: unchanged` — a claim, and a false one,
since nothing had checked. The workflow builds the summary with `if: always()`
precisely so it appears on a failed run, and what appeared was reassurance.
`unfinished()` now leads the report with the step that broke and the tail of its
error; the signal is the absence of `diff_report`, refresh.py's last step, which
catches every way the chain can stop rather than guessing what the failure
printed.

**An empty result says which thing found nothing.** Every zero-row state used
to read "Nothing matches those filters." beside a Clear filters button —
including a typo in the search box, which is the commonest way to reach zero
rows and the one case where clearing filters cannot help. The branch turns on
whether the term matches anything *ignoring* the filters, not on whether
filters are set: `bookableBy` is on by default, so "are filters set" is always
true and distinguishes nothing. A term that matches nothing is sent to the
spelling; a term the filters removed says so and counts them
(*"'ai fiori' matches 1 restaurant, but the filters remove them all"*), which
is the case where Clear filters is the right move.

**No URL reaches an `href` without its scheme being checked.** Two do on the
roster: an award record's `source_url`, and `rw.reserve`, which falls back to
`restaurants.website` — a field the listing API supplies and the weekly refresh
writes into the payload with nobody reading it. `app.js` guards this in eight
places with `isHttpURL`; `venues.js` guarded it in none, on the same data. A
poisoned payload put two live `javascript:` links on the page, one of them the
Book button. Today's payload is clean (2,576 https, one http), so this was
latent — which is the only reason it survived being read this many times.

**Neither page scrolls sideways on a phone.** Both did at 320px — an iPhone SE
— and it was one element on each, the same one: `select.sortSel`. A `<select>`
is as wide as its longest option ("Standing — strongest recognition first"
measured 345px), and the phone rule gave it `flex: 1 1 auto; max-width: none`.
Neither of those lets it shrink: a flex item will not go below its own content
without `min-width: 0`, which is the whole fix. On a list you read by scrolling
vertically, a page that also moves horizontally is the worst kind of wrong.

**Emptying the results list is only half of clearing it.** `RENDERED` is the
cursor `renderPage()` appends from, and an IntersectionObserver on `#showMore`
fires whenever the page gets short. A path that emptied `#rows` without
resetting the cursor therefore got the *next* page appended into an empty
list — a failed season switch said "Still showing Summer 2026" and came back
starting at row 51, with the top three ranked picks silently absent.
`clearRows()` pairs the two so a caller cannot do one without the other, and a
test asserts nothing else spells the clear out.

**So does the payload fetch, on both pages.** `res.ok` needs a response and a
thrown network error needs a response; neither arrives when a request is
accepted and then abandoned, so a fetch with no deadline waits without limit.
The roster's error path is well written and could not be reached that way — the
page sat blank, without even a loading line. Both pages now bound the fetch
(`FETCH_TIMEOUT_MS`), say what they are waiting for, and turn a stall into the
same sentence a 404 gets. This is the hole the Leaflet load had; the payload
fetch behind it was not checked at the time.

**The map fails visibly or not at all.** Leaflet and CARTO are the only
third-party assets on the site and they load on first opening the map. A script
tag fires `error` when a request is *refused* and nothing at all when it is
accepted and never answered — which is how a stalled CDN, a captive portal or a
phone on one bar actually behaves — so `loadLeaflet` could hang forever and the
panel sat blank with no message. It now has a deadline
(`LEAFLET_TIMEOUT_MS`), says "Loading the map…" while it waits, and does not
cache a rejection, so switching back to the map retries instead of failing
instantly for the rest of the session.

**A programme has two ends and the planner guards both.** `dateIssue` refused a
date in the past and a date after the restaurant's window closed; it never
refused one *before the programme opened*. The listing appears when a season is
announced, weeks ahead, so between announcement and opening day every date from
today onwards was offered as bookable — on 1 December, for a season starting 19
January, the planner offered Tue Dec 1 and 63 more. `season_start` had been in
the payload the whole time and nothing read it.

Not only a changeover problem: driving the live payload at 1 July, three weeks
before this season's own 20 July start, showed the same thing. Note that
`seasonPhase()` still has no *upcoming* state — before a season opens the page
reads "core" and the countdown tile counts down to `program_end` — which is a
separate judgement about copy and is left as it is.

**Sunday and Sunday dinner are different facts, and the listing carries both.**
Its whole meal-type vocabulary is four strings — `$N Dinner Price`, `$N Lunch
Price`, `$N Sunday Dinner Price`, `$N Sunday Lunch/Brunch Price` — so it already
says, per restaurant, whether Sunday is dinner or only brunch. 24 carry a Sunday
lunch/brunch price and no Sunday dinner price. `sunday_dinner` is derived from
that (`true` / `false` / `null`, where null means there is no Sunday
participation to describe), and the planner reads it. It used to read a hand-set
`no_sunday_dinner` flag that exactly one restaurant carries — and that one's
`sunday` is false, so the line above it returns first and the branch had never
run for anybody, while the other 23 were offered a Sunday with no qualification.
The flag still wins where it is set.

**A closure has two kinds and the page says which.** Google answers
`OPERATIONAL`, `CLOSED_TEMPORARILY`, `CLOSED_PERMANENTLY`, or nothing at all.
`google.closed` means **permanently** closed and nothing else; `google.status`
carries the verbatim answer so the page quotes the source instead of
paraphrasing it, and an absent answer is `null`, not `false`. Collapsing all
four into one boolean had the dashboard render a red *"permanently closed"*
pill, titled *"Google reports this location as permanently closed"*, over a
record that says `CLOSED_TEMPORARILY`. On the roster a temporary closure keeps
the closed pill's colour but loses the line-through, which is that page's mark
for gone for good.

**ToS enforcement is in code, not convention.** `assert_tos_clean()` fails the
export rather than publish a banned field. Menu text may leave only as dish-tag
snippets, and the rule is: **at most 5% of a menu's extracted text, or 40
characters, whichever is greater**, re-centred on the matched keyword (never
right-truncated), with overlapping and adjacent snippets pruned so they cannot
be stitched back into a passage. The exporter enforces a deliberately tighter
4.5% / 36 chars so an independent auditor measuring at the stated rule always
passes with margin. The published payload carries no `raw_text`, no `menu_items`,
and no dish/description fields.

**The `keyword` on a tag is menu text too, and is budgeted like a snippet.**
`recover_keyword()` returns the matched span, and several rules in
`config/dish_tags.json` bridge two words with `[^.\n]{0,40}` — so the span is
routinely a phrase (`nigiri accompanied by a tuna`, `Tuna & Avocado Carpaccio`),
not a term. Where a snippet is published the keyword sits inside it and costs
nothing; where one was refused, the keyword used to go out unbudgeted, at
exactly the point the budget had just refused to release any more of that menu.
139 did, which put 38 restaurants over the stated rule with nothing failing.
It is now paid for out of the same allowance, and dropped when the allowance
cannot cover it — the tag keeps its name and its confidence, so nothing becomes
unfilterable; only the fragment goes.

`assert_snippet_budget()` enforces the **stated** rule over the finished payload
and fails the export if it is breached. That check deliberately lives in the
export path rather than only in `tests/`: a test measures the way `build_tags`
measures, so it drifts with the same bug — this one measures against the menus.

`build_tags()` walks **every** candidate match for a tag and publishes the first
that clears all three guards, rather than stopping at the first one that fails.
A refusal is about that particular snippet — it overlaps one already published,
it sits too close to one in the same menu, or it does not fit what is left of
the budget — and says nothing about a hit elsewhere on the menu. Stopping at the
first refusal left 152 tag/restaurant pairs with no text behind 345 candidate
rows. The guards themselves are unchanged, and the budget is what actually
bounds how much of a menu can leave.

Coverage today (`python src/export_site_data.py --check`): 636 rows · 14 verified
gaps · 336 estimates · 286 with no comparable (left blank — never backfilled) ·
15 ranked picks · 195 ending by Aug 16 · 631 mappable · 616 within 12 min of a
station · 629 Google-rated (mean 4.491★) · 31 restaurants with off-site tags
(644 slugs swept) · 161 with dish tags (251 snippets pruned) · 76 with
recognition badges (3 rows suppressed) · 137 licensed outdoor. Payload
1,162,721 bytes raw, ~142 KB gzipped.

## Data caveats (read before trusting anything)

- **2 restaurants have NULL address**: `alta-calidad`, `catria-nyc` — their
  nyctourism detail pages 404/500 server-side. (`casa-brazilian` and `wagamama`
  carried this until they left the program.)
- **Pending human rulings** in `data/processed/recognition_review.json` and
  `data/processed/venue_merge_review.json`. The weekly report prints the live
  count; do not trust a number written here, which is what this line used to be.
  Originally 14 pending recognition matches
  (Masa, Roberta's, Tonchin, Ci Siamo, Madre, Carlo Mirarchi ×2, Max Sussman,
  Masa Takayama, Anne Rosenzweig ×4, Mắm) — scored below the acceptance
  threshold or name-matched with a contradicting address, awaiting a human
  ruling. None touch the final-bookings shortlist. Do not auto-accept.
- **The `norm_name()` false-positive bug is FIXED** (2026-08-05). Historically,
  `norm_name()` stripped venue types alongside genuine qualifiers, so
  "Bar Boulud" and "Café Boulud" both collapsed to `boulud` and
  "Osteria Brooklyn" lost both its words and collapsed onto "Brasserie" — 10 rows
  of James Beard awards attached to the wrong restaurants, at confidence 0.9 with
  notes `' [exact name]'`, re-created on every weekly run. Venue types are no
  longer stripped (they are precisely what separates a restaurant from its
  sibling), a name made only of qualifiers falls back to its unstripped form
  instead of the empty string, and `check_norm_name()` runs a table of
  must-collapse / must-stay-apart pairs at the top of every execution. The two
  suppression entries are retired in place (`retired: 2026-08-05`) as the record
  of why. **One suppression is still active**: `recette`, 3 James Beard rows that
  belong to Jesse Schenker's Recette in the West Village (closed ~2015), not to
  the French bistro of the same name in Williamsburg. That is a genuine namesake
  and no amount of normalisation can fix it.
- **286 restaurants have no price comparable** (publish no prices anywhere).
  HARD RULE: never backfill from delivery aggregators — markup distortion makes
  them worse than an honest blank. The gap stays a gap.
- **336 comparables are heuristic-only** (`price_sweep`, `comparable_basis =
  'heuristic'`) — nothing downstream may present them as verified.
- **OCR of the 55 image-only menus: deferred**, not attempted.
- **Listing-vs-restaurant contradictions** (restaurant's own print wins; found by
  two independent own-site passes, Aug 1): Zuma ends Aug 16 (API: Sep 6) ·
  Lincoln Tue–Sat only, ends Aug 15 or 16 (sources 1 day apart), no Sunday
  despite API · Ai Fiori & Tao Uptown Mon–Fri (API says +Sunday) · Le Pavillon is
  LUNCH-only, $60 = 2 courses (API implies $60 dinner) · Four Twenty Five
  extended to Sep 6 · Tudor City's site shows stale winter pages; its $30 lunch
  exists only in the API — dropped from bookings · Mark's Off Madison has no
  Sunday-dinner service (API flags Sunday) · Café Boulud RW posting resolved
  (JS-rendered page): $60 3-course, Mon–Thu 6–9 + Fri–Sat 5–9:30 — printed
  Saturday RW service; no Sunday · Momofuku Noodle Bar's $45 tier is a weekend
  lunch; ends Aug 30 · Code Red SETTLED at $50 by live page read (API $45 wrong;
  $60 stale metadata) · David Burke Tavern Jul 21–Aug 16 (own nav says 8/14);
  Sunday dinner unprinted · 53's $45 RW Sunday brunch vs their standing $78 dim
  sum — confirm at booking · Chito Gvrito and Playa Betty's print SATURDAY
  service despite the program-wide Saturday exclusion.
- Season windows vary by restaurant. After the Aug 10 mass extension the field
  is 401 × Sep 6, 191 × Aug 16, 32 × Aug 31, 7 × Aug 23, and single rows on
  Aug 3/14/15 and Sep 5.
- **3 restaurants geocode outside New York.** `dubuhaus` and `musaek` (both
  6 E. 32nd St., Manhattan) resolve to Oakland CA, and `the-kunjip`
  (32 W. 32nd St.) to San Angelo TX — bad lat/lng in the nyctourism detail
  pages, not a parsing error. `sane_coords()` in `export_site_data.py` nulls
  any point outside a five-borough bounding box, so the map neither misplaces
  them nor lets one bad point wreck its auto-fit. With the 2 NULL-address rows
  that leaves 631 of 636 mappable.

### Whose licence is it

The outdoor register is matched on name similarity plus distance, and a
restaurant's name frequently contains the neighbourhood it sits in — which is
enough for two businesses a block apart to look like each other. `Boucherie
Union Square` scores 0.50 against `UNION SQUARE CAFE/ DAILY PROVISIONS`, well
over the 0.34 threshold.

So the fold now takes only the rows belonging to the **same business** as the
best match. One business appearing twice — once for the pavement, once for the
roadway — is the case that fold exists for; two businesses is not. Boucherie was
published with roadway seating it does not have, credited to a `licence_name` of
`BOUCHERIE`, whose own licence is sidewalk-only. The row named its evidence and
then contradicted it.

A test checks the whole payload for that: every restaurant claiming outdoor
seating must get it from a licence naming that same business.

### The countdown counts today

`days left` is **inclusive**, because every other date test on the page is: a
restaurant ending today has not ended (`hasEnded`), still counts as urgent
(`isUrgent`), and the season is not archived until we are *past* `program_end`
(`seasonPhase`). Counting exclusively made the final day of the season read
**"0 days left"** beside "401 close by Sep 6" — a day you could still book and
eat, announced as over, on the one day the number matters most. It reads
"1 day left" now, singular.

### Counting distinct things

Two bugs in the menu parser, both the same mistake — counting occurrences of a
thing rather than distinct things.

`grade()` promises `full` means ">=2 course sections detected", but counted
entries in a list that appends a heading every time it matches. Harta parsed as
`courses: ['Desserts', 'Desserts']` and was graded `full`: the parser had found
one heading late in the PDF and swallowed everything after it as dessert items,
including `graham cracker crust` and `market berries, chantilly cream`. Seven
menus claimed a full parse on one repeated heading. Note the dashboard collapses
this to `pdf` and never showed it — the grade reaches you through
`menu_parse_quality` in `restaurants.csv`, so this is a fix to the dataset
rather than to the page.

`menu_items` held **1,041 exact duplicate rows, 11% of the table**: PDFs that
print the same section on two pages, or carry a lunch and a dinner menu under
one set of headings. Anyone counting dishes off this dataset over-counted by a
ninth. 43 duplicate tag rows went with them; no published tag changed, because
the exporter already caps snippets per tag.

Both are fixed in `parse_menus.py` and re-derived by `build_db.py` on the way
in, so the committed parse is corrected without re-downloading 473 PDFs at the
mandatory 1 req/sec.

### Asking the payload whether it agrees with itself

`tests/test_published_invariants.py` checks the *published files* against the
data they were built from, which is how every bug in this audit was found. None
of them raised, errored or logged anything; each was a number a reader could
have disproved with arithmetic.

It asks, every run:

- does the payload cover exactly the database it claims to describe, and does
  every headline count match the rows it summarises
- does any printed gap disagree with its own comparable, or any percentage with
  its own gap
- is anything plotted outside New York
- is **every** subway walk time recomputable from the raw MTA station file —
  the whole column, not a sample
- is a restaurant's outdoor seating actually held by the licence its row names
- do the published snippets stay inside the menu budget, measured against the
  raw menu text rather than against the exporter's own accounting, and can two
  of them never be stitched into one passage

The last two matter most: `assert_tos_clean()` measures what the exporter
decided to publish, which is a check marking its own homework. These measure the
published file against the menu it came from.

### Numbers a reader can check

Every figure printed beside another is derived so the two reconcile.

- **The heuristic comparable and its gaps** are both rounded from the same
  sweep figure, so they are rounded *once*: `price_sweep.gaps_for()` derives
  each gap from the already-rounded comparable. Rounding them independently gave
  a comparable of 60 with a $45 gap of 16 — 29 rows that visibly did not
  subtract. `gaps_for()` is the only definition, and **both** writers to
  `data/raw/pricesweep/` call it: `price_sweep.sweep_one` and
  `price_rescue.rescue_one`. Only the first was fixed originally, so the rescue
  pass kept writing pairs that did not subtract, and `price_sweep.py --report`
  printed them — the dashboard never was wrong, because
  `price_sweep.reconciled_gaps()` re-derives on the way into the database. The
  44 cache files that held such a pair have since been rewritten in place, so
  the arithmetic holds for anything that reads those files directly; the
  read-time re-derivation stays, for a cache written by something else.
- **The verified figures** are checked by the guard above rather than derived,
  because they are transcriptions and the pipeline must not quietly rewrite a
  human's number.
- **Mark's Off Madison** has no `comparable_usd`. The decision doc states
  `$57–68` as the **two-course** à la carte price and the saving as `$27–38
  (38–46%)` *on three*; it gives no three-course comparable, and that is what
  this field means everywhere else. It is null rather than carrying the
  two-course number, which made the row read `57 − 45 = 27`.

### The roster specifically

- **769 of 1,405 venues have never been checked against anything but a name.**
  They came from the James Beard file, which carries no addresses, so they have
  no coordinates, no rating, and an `unknown` trading status until someone spends
  a Places lookup on them. Only 634 venues can be plotted on a map at all.
- **`unknown` is not `closed`.** It means nobody has looked. The site says
  "Unverified" and explains that on hover; do not read it as a claim either way.
- **The Beard file goes back to 1991**, so the roster deliberately includes
  restaurants that closed decades ago. That is the archive working as intended —
  they keep their awards and score below anywhere still trading — but it means
  "1,414 restaurants" is not "1,414 places you can book tonight".
- **A Beard award is often to a person, not a room.** `venue_awards.person`
  carries the chef, sommelier or restaurateur, and the site prints it, because a
  roster that quietly credits the restaurant with its chef's Rising Star is
  lying by omission.
- **Michelin here is the 2025 selection only.** There is no historical Michelin
  data in this repo, so a restaurant that held a star in 2016 and lost it shows
  no Michelin recognition at all.
- **No venue on the roster is a list of restaurants or an award-body entity**,
  and a test checks that against the database rather than a fixture. The three
  that survived the automatic rules are settled by hand in
  `config/venue_aliases.json`.

## ToS rules — HARD REQUIREMENTS for any output built from this repo

Position as of **2026-08-02**, per the owner's conversation with NYC Tourism:
extracted menu TEXT is acceptable; **hosting the exact PDFs is not**.

1. **NEVER host, mirror, re-upload or commit the menu PDFs.** This is the only
   hard prohibition NYC Tourism named. `data/raw/menus/*.pdf` stays gitignored;
   menus are linked via the official S3 URL (`restaurants.menu_url`). Verify
   with `git ls-files | grep -c '\.pdf$'` — it must print 0. The weekly workflow
   checks this too and fails the run rather than commit one.
2. Rate limits stay at **≤1 req/sec** against nyctourism hosts and the S3
   bucket for any future crawl (`config.throttle()`). Do not parallelise. The
   workflow's `concurrency` group exists so two runs cannot violate this
   between processes.
3. Personal/noncommercial use. `docs/` keeps `<meta name="robots" content=
   "noindex">` — this is a personal tool, not a publication.
4. The dashboard payloads (`docs/data/seasons/*.json`, `docs/data/places.json`,
   and the temporary `docs/data/restaurants.json`) ship **derived/factual fields
   only** and carry no `raw_text`, no `menu_items` and no dish/description
   fields. That is now a design choice for payload size and honesty rather than
   a ToS requirement, and `assert_tos_clean()` in `src/export_site_data.py`
   still enforces it over all of them. Menu text appears only as short
   keyword-centred snippets (≤5% of a menu, or 40 chars, whichever is greater),
   and the same bar applies to the off-site website snippets.
5. `docs/data/venues.json` carries **no menu text at all** — not even a snippet.
   Dish tags appear as bare configured names (`game meats`), which are derived
   facts rather than menu text; `export_venues.validate()` rejects a `dishes`
   entry that is not a short string, and a test checks every published name
   against `config/dish_tags.json`.
   The roster has no use for one, so the safest thing is for the file never to
   contain the field. `export_venues.validate()` refuses to publish a row with a
   `menu`, `menu_items`, `raw_text` or `dishes` key, and a test checks the built
   payload as well as the code path. The award records in it are public facts and
   ship with the awarding body's own URL attached.

Superseded: before 2026-08-02 this section required the repo to stay private
because `menus.raw_text` lives in the committed SQLite. That requirement is
lifted by the NYC Tourism conversation above; rule 1 is not.

## Repo layout

```
src/        pipeline (config, fetch_listing, fetch_details, download_menus,
            parse_menus, build_db, tag_dishes, enrich_recognition,
            fetch_outdoor_dining, fetch_google_ratings, build_venues,
            resolve_venues, export_venues, export_site_data,
            export_places, diff_report, refresh)
            one-off / on-demand (fetch_subway, hours_lookup, price_sweep,
            price_rescue, menu_term_sweep, places_cli, job_summary)
tests/      289 pytest tests, no network; run in CI before the crawl
config/     season.json      THE ONLY FILE A CHANGEOVER EDITS
            rubric.json      composite-grade weights + cut-points
            awards.json      award sources, honour points, standing weights
            venue_aliases.json  hand rulings: not-a-restaurant, and confirmed splits
            dish_tags.json   RW-menu tag rules
            offsite_tags.json / offsite_verified.json  own-website terms + 56 hand checks
            google_place_ids.json  14 hand-pinned place_ids
            places.json      restaurants of my own, not in the programme
            shortlist.json   15 booking targets, in decision-doc rank order
            verified_values.json   hand-verified facts, merged over the API
            recognition_suppress.json  known-bad recognition matches
            secrets.example.py     (secrets.py is gitignored)
docs/       the roster (index.html, venues.js, venues.css) — the front door
            the dashboard (restaurant-week.html, app.js) — prix-fixe drilldown
            styles.css                shared by both
            data/venues.json          the roster payload
            data/seasons/<code>.json  per-season payloads
            data/seasons.json         registry
            data/places.json          my list
            data/restaurants.json     TEMPORARY legacy fallback
.github/    workflows/refresh.yml — Monday 07:00 ET cron (ACTIVE) + manual dispatch
            workflows/checks.yml  — tests + offline guards on every PR and push
data/raw/   listing snapshots+page cache · details (addresses) · recognition
            (Michelin/JB/NYT source data) · pricesweep (heuristic crawl cache) ·
            menusweep (own-website term crawl) · google (ratings cache) ·
            venues_google (roster Places cache) ·
            subway (stations) · outdoor (DOT licences) · menus/manifest.json +
            manifest_history.json + parsed.json + parsed-progress.json
            (the PDFs themselves are gitignored)
data/processed/  restaurant_week.sqlite · restaurants.csv ·
                 recognition_review.json (pending human rulings) ·
                 venue_merge_review.json (refused merges, merges made on weaker
                 evidence, folded spellings, unmatched group parts)
reports/    final bookings (decision doc) + value report (archive)
```

Known environment quirk (Cowork sandbox only): the mounted filesystem blocks
`unlink`, which strands git `index.lock` files and sqlite `-journal` files —
delete them and retry. Normal machines are unaffected. `build_db.py` and other
DB writers build in a temp dir and copy into place for the same reason; keep
that pattern.
