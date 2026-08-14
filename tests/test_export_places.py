"""The my-list payload: restaurants that were never in the program at all.

A place has to reach the dashboard in the SAME row shape a participant does --
the frontend must not learn a second vocabulary -- while every Restaurant Week
field stays empty, because there is no menu, no price, no end date and no rank
to report. What a place does get is the enrichment that is not RW-specific:
coordinates, the walk to the subway, and the same shrunk Google score, computed
by the exporter's own helpers rather than by a second copy of the formulas.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOOGLE = ROOT / "data" / "raw" / "google"
ROSTER = json.loads(
    (ROOT / "docs" / "data" / "restaurants.json").read_text(encoding="utf-8")
)["restaurants"]

PLACE = {"slug": "abc-kitchen-mine", "name": "ABC Kitchen",
         "address": "35 E 18th St, New York, NY 10003, USA",
         "place_id": "ChIJSXmIIqJZwokRIhhDroV3qX8", "notes": "never in RW"}


def configured(monkeypatch, tmp_path, places, cached=("abc-kitchen",)):
    """config/places.json + data/cache/places/, both redirected to tmp.

    The cache is written by places_cli itself off a real Google match, so the
    exporter is reading the file the CLI actually produces, not a lookalike.
    """
    import places_cli

    conf = tmp_path / "places.json"
    conf.write_text(json.dumps({"_doc": "d", "places": places}), encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    monkeypatch.setattr(places_cli, "CACHE", cache)
    for slug, place in zip(cached, places):
        matched = json.loads(
            (GOOGLE / f"{slug}.json").read_text(encoding="utf-8"))["matched"]
        places_cli.cache_record(place["slug"], place["name"], matched)
    return conf, cache


def build(monkeypatch, tmp_path, places, cached=("abc-kitchen",)):
    import export_places as p

    conf, cache = configured(monkeypatch, tmp_path, places, cached)
    monkeypatch.setattr(p, "PLACES", conf)
    monkeypatch.setattr(p, "CACHE", cache)
    return p.build_places_payload()


def test_a_place_row_carries_every_field_a_roster_row_does(monkeypatch, tmp_path):
    payload = build(monkeypatch, tmp_path, [PLACE])
    [row] = payload["places"]

    common = set.intersection(*(set(r) for r in ROSTER))
    union = set.union(*(set(r) for r in ROSTER))
    assert common <= set(row)                    # nothing the dashboard reads is missing
    assert set(row) - union == {"source"}        # and nothing new but the label
    assert row["source"] == "mine"
    assert row["slug"] == "abc-kitchen-mine" and row["name"] == "ABC Kitchen"
    assert row["notes"] == "never in RW"


def test_every_restaurant_week_field_is_empty_because_there_is_no_program(
    monkeypatch, tmp_path
):
    [row] = build(monkeypatch, tmp_path, [PLACE])["places"]

    for k in ("rw_price", "gap_usd", "gap_usd_high", "gap_pct", "gap_pct_high",
              "gap_basis", "comparable_usd", "comparable_usd_high", "price_source",
              "end_date", "end_date_source", "end_date_api", "rank", "grade",
              "rubric", "courses", "days", "sunday", "sunday_source"):
        assert row[k] is None, k
    assert row["price_tiers"] == [] and row["menu_state"] == "none"
    assert row["rubric_parts"] == {} and row["rubric_imputed"] == []
    assert row["tags"] == [] and row["recognition"] == [] and row["flags"] == []
    assert row["links"] == {"listing": None, "menu": None, "reservation": None,
                            "website": None}


def test_the_google_block_is_the_exporters_own_shrunk_score_not_a_second_formula(
    monkeypatch, tmp_path
):
    from export_site_data import GOOGLE_PRIOR, bayesian_score, build_google

    payload = build(monkeypatch, tmp_path, [PLACE])
    _, mean = build_google()
    g = payload["places"][0]["google"]
    assert g["rating"] == 4.4 and g["reviews"] == 3105
    assert g["score"] == round(bayesian_score(4.4, 3105, mean), 3)
    assert g["score"] == round(
        (3105 * 4.4 + GOOGLE_PRIOR * mean) / (3105 + GOOGLE_PRIOR), 3)
    assert g["basis"] == "confirmed" and g["closed"] is False
    assert g["place_id"] == PLACE["place_id"]
    # the score is only readable next to the mean it was shrunk toward
    assert payload["google_mean"] == round(mean, 3)
    assert payload["google_prior"] == GOOGLE_PRIOR


def test_a_place_is_put_on_the_map_and_next_to_the_subway_like_any_other_row(
    monkeypatch, tmp_path
):
    from export_site_data import load_stations, subway_for

    [row] = build(monkeypatch, tmp_path, [PLACE])["places"]
    assert (row["lat"], row["lng"]) == (40.737775, -73.9896167)
    by_route, nearest = subway_for(row["lat"], row["lng"], load_stations())
    assert row["subway"] == by_route and by_route            # Union Square is dense
    assert row["subway_nearest"] == nearest

    # a point Google puts outside the five boroughs is dropped, never plotted
    far = dict(PLACE, slug="far-away")
    conf, cache = configured(monkeypatch, tmp_path, [far])
    rec = json.loads((cache / "far-away.json").read_text(encoding="utf-8"))
    rec["matched"]["lat"], rec["matched"]["lng"] = 37.8, -122.27
    (cache / "far-away.json").write_text(json.dumps(rec), encoding="utf-8")
    import export_places as p
    monkeypatch.setattr(p, "PLACES", conf)
    monkeypatch.setattr(p, "CACHE", cache)
    [row] = p.build_places_payload()["places"]
    assert row["lat"] is None and row["lng"] is None and row["subway"] == {}


def test_a_place_with_no_google_match_yet_is_still_a_row(monkeypatch, tmp_path):
    [row] = build(monkeypatch, tmp_path, [PLACE], cached=())["places"]

    assert row["google"] is None                 # unknown, never a zero
    assert row["lat"] is None and row["address"] == PLACE["address"]
    assert row["name"] == "ABC Kitchen"


def test_an_empty_list_still_publishes_a_payload_the_frontend_can_fetch(
    monkeypatch, tmp_path
):
    payload = build(monkeypatch, tmp_path, [], cached=())
    assert payload["places"] == []
    assert payload["generated_at"]


def test_the_tos_guard_reads_a_places_payload_as_well_as_a_roster_one():
    from export_site_data import assert_tos_clean

    assert assert_tos_clean({"places": [{"slug": "x", "tags": [],
                                         "offsite_tags": []}]}) is True
    with pytest.raises(SystemExit, match="banned key"):
        assert_tos_clean({"places": [{"slug": "x", "raw_text": "the whole menu"}]})
