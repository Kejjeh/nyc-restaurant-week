# Handoff: run the Google Places resolution (issue #2)

You are picking this up in a **local** Claude Code session, in a clone of
`Kejjeh/nyc-restaurant-week`. A remote session did the preparation and cannot do
this step, because it requires a Google Places API key and that key must never
enter a cloud container or the repository.

**Your job:** run a billed, one-time API job (~709 calls, ~$23), sanity-check the
results, commit the cache, re-export, and report what changed.

Read this whole file before running anything. Step 3 spends money.

It lives at the repo root, so `git pull && cat HANDOFF-places-run.md` is enough
to pick it up.

---

## Why this exists

The roster is 1,340 venues. 636 come from the Restaurant Week listing and arrive
with an address and coordinates. The other 704 come from the James Beard,
Michelin and NYT award files — and the Beard file carries **no addresses at
all**. Those venues have never been checked against anything but their own name.

They therefore have: no coordinates, no rating, and `status = 'unknown'`, which
the site renders as "Unverified" and explicitly does not present as open or
closed. `resolve_venues.py --fetch` is the one step that can fill those in.

`unknown` is not `closed`. It means nobody has looked.

---

## Starting state (verify before you begin)

```
git pull
git status --short          # expect: empty
python -m pytest tests -q   # expect: 455 passed, or more
python src/resolve_venues.py --report
```

Don't pin the commit — this file lives in the repo, so the commit that added it
is already later than the one it was written against (`3f27c0b`). What matters
is the working tree being clean, the tests passing, and the `--report` numbers
below.

`--report` should print:

```
cache: 0 records in data/raw/venues_google
venues without coordinates: 709
  status  unknown: 704
  status     open: 636
```

If the cache count is not 0, someone has already run part of this — read
`data/raw/venues_google/` and skip ahead rather than starting over. The fetch is
resumable and will not re-buy anything already cached.

> 709 vs 704 is not a discrepancy. 704 have `status = 'unknown'`; 709 have no
> coordinates. The extra five have a known status from another source but still
> no location.

---

## What has already been verified (don't redo it)

- **The apply path works.** It was rehearsed against a temp database with three
  synthetic cache records — an accepted OPERATIONAL result, an accepted
  CLOSED_PERMANENTLY result, and a rejected one. Coordinates, rating and status
  landed correctly; the rejected venue was left `unknown` with its reason
  recorded and **no guessed values written**. Applying twice produced identical
  output, so it is idempotent.
- **`--fetch` refuses safely with no key** — it prints a message and makes no
  network call. You cannot accidentally send a request with an empty key.
- **`--dry-run` needs no key and sends nothing.**

---

## The run

### 1. Preview — free, no key

```
python src/resolve_venues.py --dry-run | less
```

Prints the exact Text Search string every venue would be sent, and the bill.
Expect **709 venues, $22.69** at $32/1000 calls.

Lines marked `!` (399 of them) are venues where **we hold no address**, so only
the name and the NYC bounding box can confirm a match. Those are the ones that
can silently attach the wrong restaurant. Skim them.

### 2. The key — this shell only

```
export GOOGLE_PLACES_KEY="..."
```

Restrict the key to the Places API in Cloud Console first. An unrestricted key
is a billing liability the moment it leaks.

Do **not** write it into `config/secrets.py` unless you want it on disk (that
path is gitignored and supported, but the env var is preferred). Never paste the
key into a chat message, a commit, or a file you might `git add`.

### 3. Spend a dollar before you spend twenty

```
python src/resolve_venues.py --fetch --limit 30
```

~30 calls, about $1. Then read what it decided:

```
python src/resolve_venues.py --report
```

**Stop here and actually read the 30 lines the fetch printed.** Each is either
`OK` or `--` with a reason. The accept rule (`judge_no_coords`) is deliberately
strict and refuses more than it accepts:

| Situation | Verdict |
| --- | --- |
| Result outside the NYC bounding box | **rejected** — "outside New York City" |
| We hold an address, street numbers intersect | accepted |
| We hold an address, ZIP matches | accepted |
| We hold an address and it disagrees | **rejected** — never overridden by a good name match |
| No address on either side, name similarity ≥ 0.62 | accepted |
| No address, name similarity < 0.62 | **rejected** |

A rejection is a *good* outcome when the match was wrong. The design principle,
from the source: an unresolved venue is a visible gap someone can fix; a wrongly
resolved one silently attaches a rating, a location and an OPEN badge to the
wrong restaurant.

What to look for in the 30:
- Any `OK` where the matched name is obviously a different restaurant.
- Old, long-closed restaurants (the Beard file goes back to 1991) matching a new
  business at the same address. This is the failure mode most likely to be wrong
  and most likely to look right.
- A cluster of `--` all giving the same reason may mean the query builder is
  wrong for a whole class of venue, not that the venues are unknown.

**If a match looks wrong, stop and report before running the rest.** Delete the
offending `data/raw/venues_google/<slug>.json` so it is not applied.

### 4. The rest

```
python src/resolve_venues.py --fetch
```

~680 more calls, ~$22. It paces itself at `PAUSE = 0.12s` between calls, so
expect roughly 5–10 minutes. It prints one line per venue.

Resumable: results cache per slug and `--fetch` skips any venue that already has
a cache file. If it dies halfway, just run it again — you will not be billed
twice for the same venue.

### 5. Re-export

**`--fetch` has already applied the cache and written the database.** You do not
need to run `resolve_venues.py` again. You do need the exporters:

```
python src/export_venues.py
python src/export_site_data.py
python src/export_places.py
python -m pytest tests -q
```

> **Trap, hit for real during preparation:** do **not** run `build_venues.py`
> after this without immediately running `resolve_venues.py` again.
> `build_venues` rebuilds the roster from scratch and blanks `place_id`,
> `rating` and coordinates; `resolve_venues` re-applies them from the cache.
> That is why `src/refresh.py` runs them in that order. Running `build_venues`
> alone silently regresses 629+ rows.

### 6. Commit

```
git add data/raw/venues_google docs/data data/processed data/venue_slugs.json
git status --short          # read this before committing
git commit -m "Places lookups for the award-only roster"
git push
```

**Committing `data/raw/venues_google/` is the point.** It is deliberately not
gitignored: it is what lets CI and every other clone apply the result with no key
and no spend. `resolve_venues.py` with no flags reads only that cache.

Before you commit, confirm the key is not in the diff:

```
git diff --cached | grep -i -E 'AIza|places_key|api[_-]?key' || echo "clean"
git ls-files | grep -c '\.pdf'     # must print 0
```

---

## What to report back

The remote session will pick this up from the pushed commit, but these numbers
are the interesting part and are easiest to read right after the run:

1. **How many resolved vs rejected** — the last line of the fetch output
   (`applied N resolved, M left unresolved, K marked closed`).
2. **How many came back permanently closed.** This is the single most valuable
   thing the run produces. The roster deliberately includes restaurants that
   closed decades ago, and until now nothing in the pipeline could tell a closed
   restaurant from an unchecked one. Note that `CLOSED_TEMPORARILY` also maps to
   `closed` in the database but is rendered differently on the site.
3. **Mappable count before and after** — was 631 of 1,340.
4. **Anything in `--report` that looks wrong**, and any cache file you deleted.
5. **The actual bill** from the Cloud Console, against the $22.69 estimate.

---

## Rules that still apply

- Never commit `GOOGLE_PLACES_KEY` or `config/secrets.py`.
- Never commit a menu PDF. `git ls-files | grep -c '\.pdf'` must print `0`.
- Rate limits of ≤1 req/sec apply to the nyctourism hosts and the S3 bucket, not
  to Google. Don't parallelise the fetch anyway — the pacing is deliberate.
- Work on a branch and push there; don't commit directly to `main` if the repo's
  convention says otherwise.

---

## If you get stuck

- `python src/resolve_venues.py --report` is safe, free and idempotent — run it
  any time to see where you are.
- `python src/resolve_venues.py` (no flags) re-applies the cache from scratch;
  also free and idempotent.
- The full documentation for this step is in `README.md` under **Resolving venues
  against Google (`src/resolve_venues.py`)** → *Running the fetch yourself*.
- Issue #2 in the repo has the original framing.
