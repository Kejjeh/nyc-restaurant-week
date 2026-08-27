"""Resolve award venues against Google Places: address, coordinates, open/closed.

Usage: python src/resolve_venues.py [--fetch] [--force] [--report] [--limit N]
  (no flag)  apply whatever is already cached to the DB -- no network, no key
  --fetch    look up venues that are still unresolved (needs GOOGLE_PLACES_KEY)
  --force    re-fetch venues that are already cached; this re-bills
  --report   print the cache's state and stop
  --limit N  fetch at most N venues this run

Why this is a separate script from fetch_google_ratings.py, which does a very
similar thing: that one resolves Restaurant Week participants, and every one of
those arrives with coordinates from the listing. It can therefore judge a
candidate on DISTANCE, which is strong evidence, and refuse anything more than
400m away.

The venues here have no coordinates at all. Most of them come from the James
Beard file, which is 1,363 records spanning 35 years and does not carry a single
address. There is nothing to measure a distance against, so this file has to
establish identity from the name, the city hint, and whether the result even
lands inside New York. That is weaker evidence, and it is graded as such:
`resolution` on every venue says exactly what was and was not checked.

Applying is split from fetching on purpose. CI has no key -- it is gitignored
and must never reach a public repo -- so a run there applies the committed cache
and changes nothing else, rather than failing or silently skipping the step.

Cache: data/raw/venues_google/{venue_slug}.json, in the same record shape
data/raw/google/ uses, so a venue and a participant read through one code path.
"""
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from build_venues import postal_key
from enrich_recognition import street_key
from fetch_google_ratings import (DETAIL_FIELDS, DETAILS_URL, TEXT_URL, api_key,
                                  flatten, get, name_sim)

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
CACHE = ROOT / "data" / "raw" / "venues_google"

# The same generous five-borough box the exporter uses. A "Balthazar" that
# geocodes to New Jersey is not our Balthazar, and one bad point ruins the
# auto-fit of the map as well as the row.
NYC_BOUNDS = (40.45, 41.02, -74.30, -73.65)

# With no coordinate to corroborate, the name has to carry the identification,
# so the bar is higher than fetch_google_ratings.py's 0.55 rescue threshold.
NAME_MIN_NO_COORDS = 0.62
PAUSE = 0.12

STATUS_FROM_GOOGLE = {
    "OPERATIONAL": "open",
    "CLOSED_TEMPORARILY": "closed",
    "CLOSED_PERMANENTLY": "closed",
}


def in_nyc(lat, lng):
    if lat is None or lng is None:
        return False
    lo_lat, hi_lat, lo_lng, hi_lng = NYC_BOUNDS
    return lo_lat <= lat <= hi_lat and lo_lng <= lng <= hi_lng


def judge_no_coords(cand, name, address):
    """-> (accepted, reason). Identity from name, place, and address if we have one.

    Deliberately refuses more than it accepts. An unresolved venue is a visible
    gap someone can go and fix; a wrongly resolved one silently attaches a
    rating, a location and an OPEN badge to the wrong restaurant.
    """
    if not in_nyc(cand.get("lat"), cand.get("lng")):
        return False, "result is outside New York City"
    sim = name_sim(name, cand.get("name"))
    if address:
        ours, theirs = street_key(address), street_key(cand.get("address"))
        if ours and theirs and ours & theirs:
            return True, f"name {sim:.2f}, street number agrees"
        if postal_key(address) and postal_key(address) == postal_key(cand.get("address")):
            return True, f"name {sim:.2f}, postal code agrees"
        return False, f"we hold an address and it disagrees (name {sim:.2f})"
    if sim >= NAME_MIN_NO_COORDS:
        return True, f"name {sim:.2f} in NYC, no address on either side to check"
    return False, f"name similarity {sim:.2f} is too low to identify on its own"


def fetch_one(venue, key):
    slug, name = venue["venue_slug"], venue["name"]
    rec = {"slug": slug, "query_name": name, "matched": None, "accepted": False,
           "reason": None, "source": None, "error": None,
           "query_address": venue.get("address")}
    try:
        if venue.get("place_id"):
            d = get(DETAILS_URL, {"place_id": venue["place_id"], "key": key,
                                  "fields": DETAIL_FIELDS})
            if d.get("status") != "OK":
                rec["error"] = f"details {d.get('status')}"
                return rec
            rec.update(matched=flatten(d["result"]), accepted=True,
                       reason="hand-verified place_id", source="place_id")
            return rec
        hint = venue.get("address") or venue.get("borough") or ""
        q = " ".join(x for x in (name, hint, "New York") if x)
        d = get(TEXT_URL, {"query": q, "key": key})
        rec["source"] = "textsearch"
        if d.get("status") == "ZERO_RESULTS":
            # Google not knowing a restaurant is suggestive for a 1994 award
            # and meaningless for a 2026 one, so it is recorded, not acted on.
            rec["reason"] = "no result for the query"
            return rec
        if d.get("status") != "OK":
            rec["error"] = f"{d.get('status')}: {d.get('error_message', '')[:120]}"
            return rec
        best = None
        for res in d["results"][:5]:
            c = flatten(res)
            ok, why = judge_no_coords(c, name, venue.get("address"))
            score = (ok, name_sim(name, c.get("name")))
            if best is None or score > best[0]:
                best = (score, (c, ok, why))
        if best is None:
            rec["reason"] = "no usable result"
            return rec
        c, ok, why = best[1]
        rec.update(matched=c, accepted=ok, reason=why)
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return rec


def write_cache(rec):
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp()) / f"{rec['slug']}.json"
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    shutil.copyfile(tmp, CACHE / f"{rec['slug']}.json")


def unresolved(con):
    """Venues with no coordinates of their own. Restaurant Week rows already
    have them from the listing, so this is the award-sourced roster."""
    return [dict(r) for r in con.execute(
        "SELECT venue_slug, name, address, borough, place_id FROM venues"
        " WHERE lat IS NULL OR lng IS NULL ORDER BY prestige DESC, venue_slug")]


def apply_cache(con, quiet=False):
    """Fold every cached record into venues. Idempotent; no network."""
    applied = closed = rejected = 0
    for f in sorted(CACHE.glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        row = con.execute("SELECT venue_slug, status_source FROM venues"
                          " WHERE venue_slug = ?", (rec["slug"],)).fetchone()
        if not row:
            continue          # a venue that has since been folded or renamed
        if not rec.get("accepted"):
            rejected += 1
            con.execute("UPDATE venues SET resolution = ? WHERE venue_slug = ?",
                        (f"unresolved: {rec.get('reason') or rec.get('error')}",
                         rec["slug"]))
            continue
        m = rec["matched"] or {}
        status = STATUS_FROM_GOOGLE.get(m.get("business_status"))
        if status == "closed":
            closed += 1
        con.execute(
            "UPDATE venues SET address = COALESCE(address, ?), lat = ?, lng = ?,"
            " place_id = ?, rating = ?, user_ratings_total = ?,"
            " status = COALESCE(?, status),"
            " status_source = COALESCE(?, status_source), resolution = ?"
            " WHERE venue_slug = ?",
            (m.get("address"), m.get("lat"), m.get("lng"), m.get("place_id"),
             m.get("rating"), m.get("user_ratings_total"),
             status,
             f"google: {m.get('business_status')}" if status else None,
             f"google places: {rec.get('reason')}", rec["slug"]))
        applied += 1
    if not quiet:
        print(f"applied {applied} resolved, {rejected} left unresolved, "
              f"{closed} marked closed")
    return applied, rejected, closed


def report(con):
    cached = list(CACHE.glob("*.json"))
    todo = unresolved(con)
    print(f"cache: {len(cached)} records in {CACHE.relative_to(ROOT)}")
    print(f"venues without coordinates: {len(todo)}")
    for row in con.execute(
        "SELECT status, COUNT(*) FROM venues GROUP BY 1 ORDER BY 2 DESC"
    ):
        print(f"  status {row[0]:>8}: {row[1]}")
    if todo:
        print("\nnext up (highest prestige first):")
        for v in todo[:10]:
            print(f"  {v['venue_slug'][:34]:35} {(v['address'] or '(no address)')[:40]}")


def main():
    tmp = Path(tempfile.mkdtemp()) / DB.name
    shutil.copyfile(DB, tmp)
    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row

    if "--report" in sys.argv:
        return report(con)

    if "--fetch" in sys.argv:
        key = api_key()
        force = "--force" in sys.argv
        todo = [v for v in unresolved(con)
                if force or not (CACHE / f"{v['venue_slug']}.json").exists()]
        if "--limit" in sys.argv:
            todo = todo[:int(sys.argv[sys.argv.index("--limit") + 1])]
        print(f"{len(todo)} venues to look up", flush=True)
        for n, v in enumerate(todo, 1):
            rec = fetch_one(v, key)
            write_cache(rec)
            m = rec["matched"] or {}
            print(f"  [{n}/{len(todo)}] {v['venue_slug']}: "
                  f"{'OK ' if rec['accepted'] else '-- '}"
                  f"{m.get('business_status') or '?'} "
                  f"{rec['reason'] or rec['error'] or ''}", flush=True)
            time.sleep(PAUSE)

    apply_cache(con)
    con.commit()
    con.close()
    shutil.copyfile(tmp, DB)


if __name__ == "__main__":
    main()
