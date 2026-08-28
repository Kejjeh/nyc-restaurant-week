"""Awards to a person, which are not awards to a room.

Found while measuring something else: 9 of the 12 oldest top honours on the
roster belonged to people. Anthony Bourdain was a venue. So were Craig
Claiborne, Bobby Flay and Gael Greene -- 72 in all, carrying prestige scores of
53-77, which put them above most of the restaurants they sat next to.

The cause was one fallback: `e.get(spec["name_field"]) or e.get("name")`. For
michelin and nyt, name_field IS "name", so the fallback could never fire. For
James Beard, name_field is "restaurant" and "name" is the HONOREE.
"""
import json
import sqlite3
from pathlib import Path

from build_venues import load_awards_config

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
RAW = ROOT / "data" / "raw" / "recognition"


def _person_only_records():
    cfg = load_awards_config()
    out = []
    for source, spec in cfg["sources"].items():
        f = RAW / spec["file"]
        if not f.exists():
            continue
        for e in json.loads(f.read_text(encoding="utf-8")):
            if not e.get(spec["name_field"]):
                out.append((source, e))
    return out


def test_every_record_without_a_venue_name_names_a_person():
    """The premise of the fix. If a record with no name_field ever carried a
    real venue name in some other field, dropping it would lose a restaurant."""
    recs = _person_only_records()
    assert recs, "no person-only records; this test would pass vacuously"
    for source, e in recs:
        assert source == "james_beard", f"{source} now has records with no venue name"
        assert e.get("name"), "a record with neither a restaurant nor a person"


def test_no_honoree_is_on_the_roster_as_a_restaurant():
    names = {e["name"] for _, e in _person_only_records()}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        venues = {n for (n,) in con.execute("SELECT name FROM venues")}
    finally:
        con.close()
    leaked = names & venues
    assert not leaked, f"people on the roster as restaurants: {sorted(leaked)}"


def test_the_honorees_are_recorded_rather_than_silently_dropped():
    """A dropped award has to be findable, or the roster has quietly decided
    something a human cannot review."""
    review = json.loads(
        (ROOT / "data" / "processed" / "venue_merge_review.json").read_text(encoding="utf-8"))
    dropped = review.get("awards_to_a_person_with_no_room", [])
    assert len(dropped) == len(_person_only_records())
    assert all(d.get("person") and d.get("reason") for d in dropped)


def test_dropping_them_took_no_award_from_a_real_restaurant():
    """These records name no room, so no venue should have lost anything. If a
    survivor's award_count moved, the fallback was carrying real data."""
    names = {e["name"] for _, e in _person_only_records()}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        orphaned = con.execute(
            "SELECT COUNT(*) FROM venue_awards WHERE matched_name IN "
            "(" + ",".join("?" * len(names)) + ")", sorted(names)).fetchone()[0]
    finally:
        con.close()
    assert orphaned == 0, "an award is still attached under an honoree's name"


def test_the_fallback_was_dead_for_every_other_source():
    """Why this is a removal rather than a category list: for michelin and nyt,
    name_field is already "name", so the fallback could never have fired."""
    cfg = load_awards_config()
    for source in ("michelin", "nyt"):
        assert cfg["sources"][source]["name_field"] == "name"
        assert not cfg["sources"][source].get("person_field")
