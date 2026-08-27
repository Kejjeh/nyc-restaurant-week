"""Resolve award venues against Google Places: address, coordinates, open/closed.

Usage: python src/resolve_venues.py [--fetch] [--force] [--report] [--dry-run] [--limit N]
  (no flag)  apply whatever is already cached to the DB -- no network, no key
  --fetch    look up venues that are still unresolved (needs GOOGLE_PLACES_KEY)
  --dry-run  print the exact queries --fetch would send, and what they would
             cost, without sending any of them or needing a key
  --force    re-fetch venues that are already cached; this re-bills
  --report   print the cache's state and stop
  --limit N  fetch (or dry-run) at most N venues this run

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
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from build_venues import postal_key
from config import in_nyc
from enrich_recognition import street_key
from fetch_google_ratings import (DETAIL_FIELDS, DETAILS_URL, TEXT_URL, api_key,
                                  flatten, get, name_sim)

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
CACHE = ROOT / "data" / "raw" / "venues_google"
# The participants' own cache, written by fetch_google_ratings.py in the
# SAME record shape -- which is the whole reason this file can read it.
PARTICIPANTS = ROOT / "data" / "raw" / "google"

# With no coordinate to corroborate, the name has to carry the identification,
# so the bar is higher than fetch_google_ratings.py's 0.55 rescue threshold.
NAME_MIN_NO_COORDS = 0.62
PAUSE = 0.12

# Places Text Search, US list price at the time of writing, per 1,000 calls.
# Only ever used to print an estimate before spending anything; nothing in the
# pipeline depends on it being current.
TEXT_SEARCH_USD_PER_1000 = 32.0

# The roster has three states and Google has three answers, but they are not
# the same three. A temporary closure is not "still trading" and is not a
# closure either, so it maps to `closed` -- the honest reading of "you cannot
# eat here now" -- and status_source carries which kind it was, verbatim, so
# the page can say "Temporarily closed" instead of striking the restaurant
# through as gone. Flattening both into one word is how the dashboard came to
# tell a reader that Antica Pesa had closed permanently while its own cited
# source said CLOSED_TEMPORARILY.
STATUS_FROM_GOOGLE = {
    "OPERATIONAL": "open",
    "CLOSED_TEMPORARILY": "closed",
    "CLOSED_PERMANENTLY": "closed",
}


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


# Anything that already places the query in the city, so "New York" is not
# stapled onto the end of a string that ends in "New York, NY 10022".
PLACED = re.compile(r"\b(new york|manhattan|brooklyn|queens|bronx|staten island)\b",
                    re.I)


def query_for(venue):
    """The exact Text Search string fetch_one() would send for this venue.

    Shared with --dry-run rather than reimplemented there, so what the dry run
    shows is what the billed run sends. A dry run that builds its own query is
    worse than no dry run: it invites confidence in something never tested --
    and this function's duplicate-city bug is exactly what the dry run caught,
    on its first execution, before a cent had been spent.

    The city is appended only when nothing already supplies it. A venue with a
    full address does not need "New York" bolted onto a string that already ends
    in "New York, NY 10022"; a Beard venue with nothing but a name does.
    """
    hint = venue.get("address") or venue.get("borough") or ""
    parts = [venue["name"], hint]
    if not PLACED.search(f"{venue['name']} {hint}"):
        parts.append("New York")
    return " ".join(x for x in parts if x)


def dry_run(con, limit=None):
    """What --fetch would do, and what it would cost, without doing any of it.

    This exists because the run it previews costs real money and cannot be
    undone, and the person paying should be able to read the queries first --
    a venue with no address and a generic name is exactly where a Text Search
    goes somewhere unexpected.
    """
    todo = [v for v in unresolved(con)
            if not (CACHE / f"{v['venue_slug']}.json").exists()]
    total = len(todo)
    shown = todo[:limit] if limit else todo
    print(f"{total} venues would be looked up "
          f"({sum(1 for v in todo if v.get('address'))} of them with an address "
          f"to corroborate the result, {sum(1 for v in todo if not v.get('address'))} "
          f"on the name alone).")
    print(f"estimated cost: ${total * TEXT_SEARCH_USD_PER_1000 / 1000:.2f} "
          f"at ${TEXT_SEARCH_USD_PER_1000:.0f}/1000 Text Search calls, "
          f"billed once — results cache per slug.\n")
    for v in shown:
        flag = " " if v.get("address") else "!"
        print(f" {flag} {v['venue_slug'][:30]:31} {query_for(v)}")
    if limit and total > len(shown):
        print(f"\n… and {total - len(shown)} more not listed "
              f"(drop --limit to see them all)")
    print("\n! = no address on our side, so only the name and the NYC bounds "
          "can confirm the match.\n  Those are the ones worth reading before "
          "you spend anything.")


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
        d = get(TEXT_URL, {"query": query_for(venue), "key": key})
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


def apply_participant_cache(con, quiet=False):
    """Fold the Restaurant Week participants' Google records into venues.

    `unresolved()` skips those rows on purpose -- they arrive from the listing
    with coordinates, so they never needed a Places lookup. But coordinates
    are not the only thing a Places record carries, and the ratings for 629 of
    them have been sitting in data/raw/google/ all along, published on the
    dashboard next door, while `venues.rating` was NULL for all 1,414 rows.

    The roster offers a "Rating" sort. With no venue rated, weightedRating()
    returned null for every row, the sort silently fell through to prestige,
    and the star line on the row never rendered once.

    Only the rating, the review count and the place_id are folded. NOT the
    address or the coordinates, which the listing already supplies and
    build_venues already vets; and NOT the status, because these restaurants
    are in this season's programme and "restaurant week listing" is better
    evidence that they are trading than one Google CLOSED_TEMPORARILY badge.

    Idempotent, no network, no key. Existing values win, so a hand-run
    resolution is never overwritten by this.
    """
    by_rw = {r["rw_slug"]: r["venue_slug"] for r in con.execute(
        "SELECT rw_slug, venue_slug FROM venues WHERE rw_slug IS NOT NULL")}
    applied = 0
    if not PARTICIPANTS.exists():
        return 0
    for f in sorted(PARTICIPANTS.glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        slug = by_rw.get(rec.get("slug"))
        if not slug or not rec.get("accepted"):
            continue
        m = rec.get("matched") or {}
        if m.get("rating") is None:
            continue
        con.execute(
            "UPDATE venues SET rating = COALESCE(rating, ?),"
            " user_ratings_total = COALESCE(user_ratings_total, ?),"
            " place_id = COALESCE(place_id, ?)"
            " WHERE venue_slug = ?",
            (m.get("rating"), m.get("user_ratings_total"), m.get("place_id"),
             slug))
        applied += 1
    if not quiet:
        print(f"applied {applied} participant ratings from "
              f"{PARTICIPANTS.relative_to(ROOT)}")
    return applied


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

    limit = (int(sys.argv[sys.argv.index("--limit") + 1])
             if "--limit" in sys.argv else None)

    if "--report" in sys.argv:
        return report(con)
    if "--dry-run" in sys.argv:
        return dry_run(con, limit)

    if "--fetch" in sys.argv:
        key = api_key()
        force = "--force" in sys.argv
        todo = [v for v in unresolved(con)
                if force or not (CACHE / f"{v['venue_slug']}.json").exists()]
        if limit:
            todo = todo[:limit]
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

    apply_participant_cache(con)
    apply_cache(con)
    con.commit()
    con.close()
    shutil.copyfile(tmp, DB)


if __name__ == "__main__":
    main()
