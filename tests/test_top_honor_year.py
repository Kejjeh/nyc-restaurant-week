"""A badge that says when.

387 of the 782 honour badges on the roster were for an award that is not from
its source's most recent selection -- "James Beard winner" with nothing after
it, for a win in 1993, on restaurants that in several cases have since closed.
Nothing said so, because top_honor is the best honour a venue has EVER held.

The year is not a claim the honour lapsed. A Michelin star is a standing
selection and does lapse; a James Beard win is an event and never does. Naming
the year is the one statement that is true of both.

Half of issue 3. The other half -- back-filling Michelin's own history, which is
2025-only, so a lost star still reads as no recognition at all -- needs data
this repo does not have.
"""
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
JS = (ROOT / "docs" / "venues.js").read_text(encoding="utf-8")
PAYLOAD = json.loads((ROOT / "docs" / "data" / "venues.json").read_text(encoding="utf-8"))


def venues():
    return PAYLOAD["venues"] if isinstance(PAYLOAD, dict) else PAYLOAD


def test_every_top_honour_carries_its_year():
    missing = [v["slug"] for v in venues()
               if v.get("top_honor_label") and not v.get("top_honor_year")]
    assert not missing, f"{len(missing)} badges have no year to show: {missing[:5]}"


def test_latest_is_computed_from_the_data_not_pinned():
    """So the Michelin back-fill moves it without a config edit."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        latest = dict(con.execute(
            "SELECT source, MAX(year) FROM venue_awards GROUP BY source"))
        rows = con.execute(
            "SELECT top_honor, top_honor_year, top_honor_is_latest FROM venues"
            " WHERE top_honor IS NOT NULL").fetchall()
    finally:
        con.close()
    for honor, year, is_latest in rows:
        source = honor.split(":")[0]
        assert bool(is_latest) == (year == latest[source]), (
            f"{honor} {year} marked is_latest={is_latest} against {latest[source]}")


def test_a_current_michelin_star_shows_no_year_and_an_old_beard_win_does():
    held = [v for v in venues() if v.get("top_honor")]
    current = [v for v in held
               if v["top_honor"].startswith("michelin") and v["top_honor_is_latest"]]
    old = [v for v in held
           if v["top_honor"].startswith("james_beard") and not v["top_honor_is_latest"]]
    assert current and old, "the roster no longer contains both cases"
    assert all(v["top_honor_year"] == 2025 for v in current[:20])
    assert all(v["top_honor_year"] < 2026 for v in old[:20])


def test_the_year_stays_out_of_the_facet_value():
    """top_honor_label is the "Highest honour" filter's option list. Folding the
    year into it would turn nine options into several hundred."""
    labels = {v["top_honor_label"] for v in venues() if v.get("top_honor_label")}
    assert len(labels) < 20, f"the honour facet has {len(labels)} options"
    assert not [l for l in labels if re.search(r"\d{4}", l)], labels


def test_the_badge_appends_the_year_only_when_it_is_not_the_latest():
    """Pinned against the rendering code, because the payload being right and
    the badge being right are two different facts."""
    m = re.search(r"top_honor_year && !v\.top_honor_is_latest", JS)
    assert m, "venues.js no longer gates the year on is_latest"
    assert "pill honor" in JS
