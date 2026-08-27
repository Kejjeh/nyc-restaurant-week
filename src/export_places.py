"""Export the my-list payload: docs/data/places.json.

Usage: python src/export_places.py [--check] [--quiet]
  --check  build and validate the payload, print the report, write nothing

These are restaurants I want to try that are NOT in Restaurant Week, kept in
config/places.json by src/places_cli.py. They reach the dashboard in the SAME
row shape a participant does, so the frontend needs no second vocabulary -- but
every Restaurant Week field is empty, because there is no menu, no price, no end
date and no rank, and inventing one would be a lie about the program.

The enrichment that is NOT program-specific is reused verbatim from the
exporter: coordinates, the subway walk, and the same Bayesian score shrunk
toward the same roster mean. A place and a participant are never scored by two
different formulas, and the ToS guard runs over this payload too.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from export_site_data import (GOOGLE, GOOGLE_PRIOR, assert_tos_clean, build_google,
                              clean, google_row, jfile, load_stations, rel,
                              sane_coords, subway_for, write_if_changed)

ROOT = Path(__file__).resolve().parents[1]
PLACES = ROOT / "config" / "places.json"
CACHE = ROOT / "data" / "cache" / "places"
OUT = ROOT / "docs" / "data" / "places.json"


def blank_row():
    """Every field a roster row carries, emptied.

    Null means UNKNOWN here exactly as it does everywhere else in this project:
    never a zero, never a false. The Restaurant Week fields are not unknown but
    INAPPLICABLE, and they are still nulled rather than dropped, so a row can be
    read without first asking which list it came from.
    """
    return {
        "borough": None, "neighborhood": None,
        "subway": {}, "subway_nearest": None, "outdoor": None,
        "cuisines": [], "price_tiers": [], "meal_periods": [], "meal_types_raw": [],
        "end_date": None, "end_date_source": None, "end_date_api": None,
        "days": None, "sunday": None, "sunday_source": None, "sunday_api": None,
        # Your own places are not in the programme, so there is no Sunday
        # prix fixe to be dinner or brunch. Null, like every other
        # Restaurant Week field here.
        "sunday_dinner": None,
        "courses": None, "rank": None, "grade": None,
        "verdict": "Not in the program", "verdict_note": None,
        "flags": [], "menu_state": "none",
        "recognition": [], "recog_top": None, "recog_rank": None, "recog_eras": [],
        "tags": [], "offsite_tags": [],
        "links": {"listing": None, "menu": None, "reservation": None,
                  "website": None},
        "gap_usd": None, "gap_usd_high": None, "gap_pct": None, "gap_pct_high": None,
        "gap_basis": None, "comparable_usd": None, "comparable_usd_high": None,
        "rw_price": None, "price_source": None,
        "rubric": None, "rubric_parts": {}, "rubric_completeness": None,
        "rubric_imputed": [],
    }


def google_record(slug):
    """The confirmed match for a place. data/cache/ is gitignored, so a record
    hand-placed in data/raw/google/ is honoured as the way to survive a clone."""
    return jfile(CACHE / f"{slug}.json") or jfile(GOOGLE / f"{slug}.json")


def place_row(place, mean, stations):
    rec = google_record(place["slug"])
    matched = (rec or {}).get("matched") or {}
    lat, lng = sane_coords(matched.get("lat"), matched.get("lng"))
    by_route, nearest = subway_for(lat, lng, stations)
    row = {
        "slug": place["slug"],
        "name": clean(place.get("name")),
        # the one field a roster row does not carry: absent means Restaurant Week
        "source": "mine",
        "address": clean(place.get("address") or matched.get("address")),
        "lat": lat, "lng": lng,
        "subway": by_route, "subway_nearest": nearest,
        "notes": place.get("notes"),
        "google": google_row(rec, mean) if rec else None,
    }
    return {**row, **{k: v for k, v in blank_row().items() if k not in row}}


def build_places_payload():
    doc = jfile(PLACES, {}) or {}
    stations = load_stations()
    _, mean = build_google()
    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        # published next to the score, because a shrunk rating is unreadable
        # without the mean it was shrunk toward
        "google_mean": round(mean, 3),
        "google_prior": GOOGLE_PRIOR,
        "places": [place_row(p, mean, stations) for p in doc.get("places", [])],
    }
    assert_tos_clean(payload)
    return payload


def main():
    check = "--check" in sys.argv
    quiet = "--quiet" in sys.argv

    payload = build_places_payload()
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    wrote = not check and write_if_changed(OUT, payload, text)

    if not quiet:
        rows = payload["places"]
        rated = sum(1 for r in rows if r["google"])
        mapped = sum(1 for r in rows if r["lat"])
        near = sum(1 for r in rows if r["subway"])
        print(f"places        {len(rows)} on my list · {rated} rated"
              f" · {mapped} mappable · {near} near a station")
        where = ("  (not written: --check)" if check
                 else f"  -> {rel(OUT)}" if wrote
                 else "  (unchanged, not rewritten)")
        print(f"payload       {len(text.encode('utf-8')):,} bytes{where}")


if __name__ == "__main__":
    main()
