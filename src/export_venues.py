"""Export the venue roster payload: docs/data/venues.json.

Usage: python src/export_venues.py [--check] [--quiet] [--allow-shrink]
  --check         build and validate, print the report, write nothing
  --quiet         suppress the summary
  --allow-shrink  write even when the roster lost more than a fifth of its rows

This payload is the site's spine. It carries every NYC restaurant the award
sources name, whether or not it is in Restaurant Week, whether or not it is
still open.

ToS: the Restaurant Week listing (nyctourism.com) is personal/noncommercial and
must not be republished, so the only listing-derived fields here are the same
DERIVED/FACTUAL ones docs/data/restaurants.json already carries -- name, place,
price tier, participation. No menu text of any kind reaches this file; a venue's
menu lives behind the official S3 link on the Restaurant Week page and nowhere
else. The award records are public facts and are exported with the source URL
attached, so every badge on the page can be traced back to whoever awarded it.
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config import PROGRAM_END, SEASON_LABEL

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
AWARDS_CONFIG = ROOT / "config" / "awards.json"
OUT = ROOT / "docs" / "data" / "venues.json"

SHRINK_LIMIT = 0.8      # refuse to publish a roster under 80% of the last one


def load(con):
    con.row_factory = sqlite3.Row
    awards = {}
    for a in con.execute(
        "SELECT venue_slug, source, level, award, year, rank, person, source_url"
        " FROM venue_awards ORDER BY year DESC, source"
    ):
        awards.setdefault(a["venue_slug"], []).append({
            "source": a["source"], "level": a["level"], "award": a["award"],
            "year": a["year"], "rank": a["rank"], "person": a["person"],
            "url": a["source_url"],
        })

    # Price tiers and meal periods come from the Restaurant Week listing and
    # only exist for the venues that are in it. Left absent, not zero: "no
    # prix fixe" and "not participating" are different facts and the site
    # filters on the difference.
    rw = {r["slug"]: r for r in con.execute(
        "SELECT slug, price_tiers, meal_periods, sunday_participation,"
        " reservation_link, website, listing_url, cuisines FROM restaurants")}

    rows = []
    for v in con.execute(
        "SELECT * FROM venues ORDER BY prestige DESC, award_count DESC, name"
    ):
        r = rw.get(v["rw_slug"]) if v["rw_slug"] else None
        rows.append({
            "slug": v["venue_slug"],
            "name": v["name"],
            "borough": v["borough"],
            "neighborhood": v["neighborhood"],
            "address": v["address"],
            "lat": v["lat"], "lng": v["lng"],
            "status": v["status"],
            "status_source": v["status_source"],
            "prestige": v["prestige"],
            "top_honor": v["top_honor"],
            "top_honor_label": v["top_honor_label"],
            "award_count": v["award_count"],
            "award_sources": json.loads(v["award_sources"] or "[]"),
            "first_award_year": v["first_award_year"],
            "last_award_year": v["last_award_year"],
            "recognition": awards.get(v["venue_slug"], []),
            "rating": v["rating"],
            "ratings_total": v["user_ratings_total"],
            "resolution": v["resolution"],
            "rw": None if not r else {
                "slug": v["rw_slug"],
                "price_tiers": json.loads(r["price_tiers"] or "[]"),
                "meal_periods": json.loads(r["meal_periods"] or "[]"),
                "sunday": bool(r["sunday_participation"]),
                "cuisines": json.loads(r["cuisines"] or "[]"),
                "reserve": r["reservation_link"] or r["website"] or r["listing_url"],
            },
        })
    return rows


def facets(rows):
    """Precomputed filter vocabularies, so the page does not have to derive them
    from 1,400 rows before it can draw a single control."""
    honors, sources, boroughs, cuisines = {}, {}, {}, {}
    for r in rows:
        if r["top_honor_label"]:
            honors[r["top_honor_label"]] = honors.get(r["top_honor_label"], 0) + 1
        for s in r["award_sources"]:
            sources[s] = sources.get(s, 0) + 1
        if r["borough"]:
            boroughs[r["borough"]] = boroughs.get(r["borough"], 0) + 1
        for c in (r["rw"] or {}).get("cuisines", []):
            cuisines[c] = cuisines.get(c, 0) + 1
    order = lambda d: dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))
    return {"top_honor": order(honors), "award_source": order(sources),
            "borough": order(boroughs), "cuisine": order(cuisines)}


def validate(rows, cfg):
    """Problems that must stop a publish, and notes that merely want saying."""
    errors, notes = [], []
    slugs = [r["slug"] for r in rows]
    if len(slugs) != len(set(slugs)):
        errors.append("duplicate venue slugs in the payload")
    honors = set(cfg["honors"])
    for r in rows:
        if r["top_honor"] and r["top_honor"] not in honors:
            errors.append(f"{r['slug']}: honor {r['top_honor']} is not in awards.json")
        if r["award_count"] != len(r["recognition"]):
            errors.append(f"{r['slug']}: award_count disagrees with the records")
        if r["award_count"] == 0 and not r["rw"]:
            errors.append(f"{r['slug']}: no awards and not in Restaurant Week")
        for k in ("menu", "menu_items", "raw_text", "dishes"):
            if k in r:
                errors.append(f"{r['slug']}: {k} must never reach this payload")
    placed = sum(1 for r in rows if r["lat"] is not None)
    notes.append(f"{placed}/{len(rows)} venues have coordinates and can be mapped")
    unresolved = sum(1 for r in rows if r["status"] == "unknown")
    notes.append(f"{unresolved} venues have an unverified open/closed status "
                 f"(run src/resolve_venues.py --fetch with a Places key)")
    return errors, notes


def main():
    check = "--check" in sys.argv
    quiet = "--quiet" in sys.argv
    cfg = json.loads(AWARDS_CONFIG.read_text(encoding="utf-8"))

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = load(con)
    finally:
        con.close()

    errors, notes = validate(rows, cfg)
    previous = 0
    if OUT.exists():
        try:
            previous = len(json.loads(OUT.read_text(encoding="utf-8"))["venues"])
        except Exception:
            previous = 0
    if previous and len(rows) < previous * SHRINK_LIMIT and "--allow-shrink" not in sys.argv:
        errors.append(f"roster fell from {previous} to {len(rows)} venues; "
                      f"pass --allow-shrink if that is real")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "season_label": SEASON_LABEL,
        "program_end": PROGRAM_END,
        "honors": {k: v for k, v in cfg["honors"].items() if not k.startswith("_")},
        "source_labels": {k: v["label"] for k, v in cfg["sources"].items()},
        "facets": facets(rows),
        "counts": {
            "venues": len(rows),
            "with_recognition": sum(1 for r in rows if r["award_count"]),
            "in_restaurant_week": sum(1 for r in rows if r["rw"]),
            "both": sum(1 for r in rows if r["rw"] and r["award_count"]),
            "open": sum(1 for r in rows if r["status"] == "open"),
            "closed": sum(1 for r in rows if r["status"] == "closed"),
            "unverified": sum(1 for r in rows if r["status"] == "unknown"),
            "mappable": sum(1 for r in rows if r["lat"] is not None),
        },
        "venues": rows,
    }

    if not quiet:
        c = payload["counts"]
        print(f"venues {c['venues']}  recognised {c['with_recognition']}  "
              f"Restaurant Week {c['in_restaurant_week']}  both {c['both']}")
        print(f"open {c['open']}  closed {c['closed']}  "
              f"unverified {c['unverified']}  mappable {c['mappable']}")
        for n in notes:
            print(f"  note: {n}")
    for e in errors:
        print(f"  ERROR: {e}")
    if errors:
        raise SystemExit(1)
    if check:
        print("--check: nothing written")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp()) / OUT.name
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    shutil.copyfile(tmp, OUT)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
