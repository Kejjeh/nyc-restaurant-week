"""Pull every restaurant record from the listing API into a dated snapshot.

The participant browser at nyctourism.com/restaurant-week is backed by
POST https://program-api.nyctourism.com/restaurant-week  {page, lookup}
returning {items (12/page), total, count, lookup}.

Per-page responses are cached in data/raw/listing/pages/ so an interrupted
run resumes where it left off. Page cache is cleared at the start of a fresh
day's snapshot (stale pages must not mix across days).
"""
import datetime
import json

from config import LISTING_DIR, MIN_ROWS, SEASON, api_post

PAGES = LISTING_DIR / "pages"
PAGES.mkdir(exist_ok=True)
STAMP_F = PAGES / "_stamp"


def validate_listing(rows, total, season, min_rows):
    """Refuse an unhealthy listing BEFORE it can overwrite latest.json."""
    if total == 0:
        raise RuntimeError("API total=0: empty listing, refusing to overwrite")
    if len(rows) < min_rows:
        raise RuntimeError(
            f"Only {len(rows)} unique records vs floor {min_rows}: listing too small"
        )
    if len(rows) < 0.9 * total:
        raise RuntimeError(
            f"Only {len(rows)} unique records vs API total {total}: pagination broke?"
        )
    sample = [u for u in (r.get("menuFileUrl") or "" for r in rows) if u][:25]
    if sample and not any(season in u for u in sample):
        raise RuntimeError(
            f"No menuFileUrl in a {len(sample)}-url sample contains {season!r}: "
            "new season? update config/season.json"
        )
    if not sample:
        # early-season listings can pre-date the menu uploads entirely
        print(f"warning: no menuFileUrl values yet (season {season} unverified)")


def fetch_all(verbose=True):
    stamp = datetime.date.today().isoformat()
    if not STAMP_F.exists() or STAMP_F.read_text() != stamp:
        for f in PAGES.glob("page-*.json"):
            f.unlink()
        STAMP_F.write_text(stamp)

    def get_page(n):
        f = PAGES / f"page-{n:03d}.json"
        if f.exists():
            return json.loads(f.read_text())
        d = api_post({"page": n, "lookup": {}})
        f.write_text(json.dumps(d))
        return d

    first = get_page(1)
    total = first["total"]
    items = list(first["items"])
    page = 2
    while len(items) < total:
        d = get_page(page)
        if not d.get("items"):
            break
        items.extend(d["items"])
        if verbose and page % 10 == 0:
            print(f"  page {page}: {len(items)}/{total}", flush=True)
        page += 1
    seen, out = set(), []
    for it in items:
        if it["slug"] not in seen:
            seen.add(it["slug"])
            out.append(it)
    if verbose:
        print(f"fetched {len(items)} rows, {len(out)} unique slugs, API total={total}")
    validate_listing(out, total, SEASON, MIN_ROWS)
    return out, first["lookup"], total, stamp


def main():
    items, lookup, total, stamp = fetch_all()
    snap = LISTING_DIR / f"snapshot-{stamp}.json"
    snap.write_text(
        json.dumps(
            {"fetched": stamp, "api_total": total, "items": items, "lookup": lookup},
            indent=1,
        )
    )
    (LISTING_DIR / "latest.json").write_text(snap.read_text())
    print(f"wrote {snap} ({len(items)} restaurants)")


if __name__ == "__main__":
    main()
