"""Characterisation of the Google matcher, pinned to records already paid for.

data/raw/google/ is the expensive part of this pipeline -- one billed Text
Search per restaurant, 645 files deep -- so a refactor that shifts the record
shape, the accepted flag or a word of the reason silently invalidates the lot.
These tests recompute real cached records from their own contents and from the
coordinates the DB held at match time, and demand the same answer back.
"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOOGLE = ROOT / "data" / "raw" / "google"
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"


def cached(slug):
    return json.loads((GOOGLE / f"{slug}.json").read_text(encoding="utf-8"))


def coords(slug):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return con.execute("SELECT lat, lng FROM restaurants WHERE slug = ?",
                           (slug,)).fetchone()
    finally:
        con.close()


def as_api_result(m):
    """A cached `matched` block turned back into the raw Places shape."""
    return {"place_id": m["place_id"], "name": m["name"],
            "formatted_address": m["address"], "rating": m["rating"],
            "user_ratings_total": m["user_ratings_total"],
            "business_status": m["business_status"],
            "geometry": {"location": {"lat": m["lat"], "lng": m["lng"]}}}


def test_flatten_still_produces_exactly_the_cached_matched_block():
    from fetch_google_ratings import flatten

    for slug in ("abc-kitchen", "100-feast"):
        m = cached(slug)["matched"]
        assert flatten(as_api_result(m)) == m
    # a result without geometry keeps the keys and nulls the point
    assert flatten({"name": "x"})["lat"] is None


def test_write_still_emits_a_cached_file_byte_for_byte(tmp_path, monkeypatch):
    import fetch_google_ratings as g

    monkeypatch.setattr(g, "OUT", tmp_path)
    # 100 Feast carries its Chinese name: ensure_ascii would rewrite this file
    g.write(cached("100-feast"))
    assert (tmp_path / "100-feast.json").read_bytes() \
        == (GOOGLE / "100-feast.json").read_bytes()


def test_judge_recomputes_the_reason_recorded_for_two_real_matches():
    from fetch_google_ratings import judge

    for slug in ("abc-kitchen", "100-feast"):
        rec = cached(slug)
        assert judge(rec["matched"], rec["query_name"], *coords(slug)) \
            == (rec["accepted"], rec["reason"])
    assert cached("abc-kitchen")["reason"] == "8m away"

    # the DUMBO branch is a real restaurant 3.8km from the Union Square one --
    # accepting it on the strength of a shared name is the bug this guards
    dumbo = cached("abc-kitchens-dumbo")["matched"]
    ok, why = judge(dumbo, "ABC Kitchen", *coords("abc-kitchen"))
    assert ok is False and why == "3806m away, name similarity 0.33"
    assert judge(dumbo, "ABC Kitchen", None, None) \
        == (False, "no coordinates on our side to corroborate")


def test_best_candidate_looks_past_the_first_hit_to_the_right_branch():
    from fetch_google_ratings import best_candidate

    here = cached("abc-kitchen")["matched"]
    dumbo = cached("abc-kitchens-dumbo")["matched"]
    lat, lng = coords("abc-kitchen")

    # Google ranks the wrong branch first; distance has to overrule the order
    got = best_candidate([as_api_result(dumbo), as_api_result(here)],
                         "ABC Kitchen", lat, lng)
    assert got == (here, True, "8m away")
    assert best_candidate([as_api_result(here)], "ABC Kitchen", lat, lng) == got

    # nothing rescues a far hit: it comes back rejected, never silently dropped
    only_far = best_candidate([as_api_result(dumbo)], "ABC Kitchen", lat, lng)
    assert only_far == (dumbo, False, "3806m away, name similarity 0.33")

    assert best_candidate([], "ABC Kitchen", lat, lng) is None
    assert best_candidate([{"name": "no geometry"}], "ABC Kitchen", lat, lng) is None
