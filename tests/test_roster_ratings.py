"""The roster's Rating sort had nothing to sort on.

`venues.rating` was NULL for all 1,414 rows, so weightedRating() returned null
for every venue, the "Google rating — weighted" sort silently fell through to
prestige for every pair, and the star line on the row never rendered once.

The ratings were not missing. 629 of the 636 Restaurant Week participants have
a Google record in data/raw/google/, published on the dashboard next door and
committed to this repo. resolve_venues.unresolved() skips those rows on purpose
-- they arrive from the listing with coordinates and never needed a lookup --
and a rating is not a coordinate. Nothing errored; a control was simply inert.

The second half is the shrinkage constant. docs/venues.js recomputed the
weighted score itself with `PRIOR = 300` while every payload in the project
uses 150, under a comment saying it gave ratings "the same treatment" the
dashboard does. On this data that reorders 452 of the 629 rated rows.
"""
import json
import sqlite3
from pathlib import Path

import pytest

import config
import resolve_venues

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "docs" / "venues.js").read_text(encoding="utf-8")
PAYLOAD = ROOT / "docs" / "data" / "venues.json"


@pytest.fixture(scope="module")
def payload():
    if not PAYLOAD.exists():
        pytest.skip("docs/data/venues.json not built")
    return json.loads(PAYLOAD.read_text(encoding="utf-8"))


# --- the fold ---------------------------------------------------------------

def venues_db(tmp_path, rows):
    db = tmp_path / "t.sqlite"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE venues (venue_slug TEXT PRIMARY KEY, rw_slug TEXT,"
                " rating REAL, user_ratings_total INTEGER, place_id TEXT,"
                " status TEXT, status_source TEXT, lat REAL, lng REAL,"
                " address TEXT)")
    con.executemany("INSERT INTO venues (venue_slug, rw_slug, status,"
                    " status_source, lat, lng, address) VALUES (?,?,?,?,?,?,?)",
                    rows)
    con.commit()
    return con


def write_records(dirpath, records):
    dirpath.mkdir(parents=True, exist_ok=True)
    for rec in records:
        (dirpath / f"{rec['slug']}.json").write_text(json.dumps(rec))


def test_a_participants_rating_reaches_its_venue(tmp_path, monkeypatch):
    con = venues_db(tmp_path, [("manhatta", "manhatta", "open",
                                "restaurant week listing", 40.7, -74.0, "28 Liberty")])
    cache = tmp_path / "google"
    write_records(cache, [{
        "slug": "manhatta", "accepted": True,
        "matched": {"rating": 4.7, "user_ratings_total": 3999,
                    "place_id": "abc", "lat": 1.0, "lng": 2.0,
                    "address": "somewhere else", "business_status": "OPERATIONAL"},
    }])
    monkeypatch.setattr(resolve_venues, "PARTICIPANTS", cache)
    assert resolve_venues.apply_participant_cache(con, quiet=True) == 1
    row = con.execute("SELECT * FROM venues").fetchone()
    assert (row["rating"], row["user_ratings_total"], row["place_id"]) \
        == (4.7, 3999, "abc")


def test_the_fold_leaves_the_listing_facts_alone(tmp_path, monkeypatch):
    """Coordinates and address come from the listing and are already vetted;
    the status comes from the programme, which is better evidence that a
    restaurant is trading than one CLOSED_TEMPORARILY badge on a place record."""
    con = venues_db(tmp_path, [("x", "x", "open", "restaurant week listing",
                                40.7, -74.0, "28 Liberty")])
    cache = tmp_path / "google"
    write_records(cache, [{
        "slug": "x", "accepted": True,
        "matched": {"rating": 4.1, "user_ratings_total": 10,
                    "lat": 0.0, "lng": 0.0, "address": "wrong",
                    "business_status": "CLOSED_TEMPORARILY"},
    }])
    monkeypatch.setattr(resolve_venues, "PARTICIPANTS", cache)
    resolve_venues.apply_participant_cache(con, quiet=True)
    row = con.execute("SELECT * FROM venues").fetchone()
    assert (row["lat"], row["lng"], row["address"]) == (40.7, -74.0, "28 Liberty")
    assert (row["status"], row["status_source"]) == ("open", "restaurant week listing")


def test_a_rejected_record_supplies_nothing(tmp_path, monkeypatch):
    con = venues_db(tmp_path, [("x", "x", "open", "l", 1.0, 2.0, "a")])
    cache = tmp_path / "google"
    write_records(cache, [{"slug": "x", "accepted": False,
                           "matched": {"rating": 5.0, "user_ratings_total": 1}}])
    monkeypatch.setattr(resolve_venues, "PARTICIPANTS", cache)
    assert resolve_venues.apply_participant_cache(con, quiet=True) == 0
    assert con.execute("SELECT rating FROM venues").fetchone()["rating"] is None


def test_an_existing_value_is_never_overwritten(tmp_path, monkeypatch):
    """A hand-run resolution must survive a re-run of this."""
    con = venues_db(tmp_path, [("x", "x", "open", "l", 1.0, 2.0, "a")])
    con.execute("UPDATE venues SET rating = 3.0, user_ratings_total = 7")
    cache = tmp_path / "google"
    write_records(cache, [{"slug": "x", "accepted": True,
                           "matched": {"rating": 5.0, "user_ratings_total": 999}}])
    monkeypatch.setattr(resolve_venues, "PARTICIPANTS", cache)
    resolve_venues.apply_participant_cache(con, quiet=True)
    row = con.execute("SELECT * FROM venues").fetchone()
    assert (row["rating"], row["user_ratings_total"]) == (3.0, 7)


def test_it_runs_in_the_no_key_path():
    """CI has no Places key. Applying must never need one."""
    import inspect
    src = inspect.getsource(resolve_venues.main)
    assert "apply_participant_cache(con)" in src
    body = src[:src.index('if "--fetch" in sys.argv')]
    assert "api_key" not in body


# --- the published roster ---------------------------------------------------

def test_the_roster_actually_carries_ratings(payload):
    rated = [v for v in payload["venues"] if v.get("rating") is not None]
    assert rated, "the Rating sort has nothing to sort on"
    assert len(rated) >= 600, f"only {len(rated)} rated venues"
    for v in rated:
        assert 0 < v["rating"] <= 5
        assert v["ratings_total"] is None or v["ratings_total"] >= 0


def test_the_shrinkage_constants_are_published(payload):
    assert payload["google_prior"] == config.GOOGLE_PRIOR
    assert payload["google_mean"] is not None
    assert payload["google_rated"] == sum(
        1 for v in payload["venues"] if v.get("rating") is not None)


def test_the_page_does_not_spell_the_prior_out():
    """It hard-coded 300 while every payload used 150, under a comment saying
    the treatment was the same as the dashboard's."""
    assert "payload.google_prior" in JS
    assert "payload.google_mean" in JS
    body = JS[JS.index("let PRIOR"):JS.index("function weightedRating")]
    assert "300" not in body, "the roster is spelling a prior out again"


def test_one_home_for_the_prior():
    """Three payloads publish a score that depends on it."""
    import export_places
    import export_site_data
    import export_venues

    assert (export_site_data.GOOGLE_PRIOR
            is export_venues.GOOGLE_PRIOR
            is export_places.GOOGLE_PRIOR
            is config.GOOGLE_PRIOR)


def test_the_roster_and_the_dashboard_agree_on_the_mean(payload):
    """Same corpus of rated restaurants, so the same mean to shrink toward."""
    dash = ROOT / "docs" / "data" / "restaurants.json"
    if not dash.exists():
        pytest.skip("dashboard payload not built")
    d = json.loads(dash.read_text(encoding="utf-8"))
    assert d["google_prior"] == payload["google_prior"]
    assert abs(d["google_mean"] - payload["google_mean"]) < 0.01
