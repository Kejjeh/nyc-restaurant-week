"""Guards the exporter needs to survive a season transition.

The dates here are pinned against the live listing, so a refactor that quietly
changes how a week label becomes an end date fails loudly instead of restamping
every restaurant.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "raw" / "listing" / "latest.json"


def weeks_for(slug):
    """The real restaurantInclusionWeek array the exporter would be handed."""
    for it in json.loads(LATEST.read_text(encoding="utf-8"))["items"]:
        if it["slug"] == slug:
            return it["restaurantInclusionWeek"]
    raise AssertionError(f"{slug} is not in the live listing any more")


def test_end_date_from_weeks_pins_the_live_listings_three_enders():
    from export_site_data import end_date_from_weeks

    # blue-fin runs the full extension: "Week 7 (Sept 1 - Sept 6)"
    assert end_date_from_weeks(weeks_for("blue-fin")) == "2026-09-06"
    # meduza-mediterrania stops at "Week 6 (Aug 24 - Aug 31)"
    assert end_date_from_weeks(weeks_for("meduza-mediterrania")) == "2026-08-31"
    # miriam-brooklyn never took the Aug 10 mass extension: one week only
    assert end_date_from_weeks(weeks_for("miriam-brooklyn")) == "2026-08-16"
    assert end_date_from_weeks([]) is None
    assert end_date_from_weeks(["Week 4"]) is None

    # The month-elided form the parser also handles -- "(Aug 24 - 31)" -- has no
    # live example, so it is pinned synthetically rather than left uncovered.
    labels = {str(w) for it in json.loads(LATEST.read_text(encoding="utf-8"))["items"]
              for w in (it.get("restaurantInclusionWeek") or [])}
    assert not [x for x in labels if x.count("Aug") == 1 and "Sept" not in x
                and "July" not in x]
    assert end_date_from_weeks(["Week 6 (Aug 24 - 31)"]) == "2026-08-31"


def test_end_date_from_weeks_takes_the_year_so_a_winter_roster_is_not_stamped_summer():
    import config
    from export_site_data import end_date_from_weeks

    # A winter season's weeks run in January. Defaulting to the summer
    # SEASON_YEAR would stamp them 2026-01-25 -- a date already in the past, so
    # every restaurant on the roster would render as closed.
    assert end_date_from_weeks(["Week 1 (Jan 19 - Jan 25)"], year=2027) == "2027-01-25"
    assert end_date_from_weeks(["Week 1 (Jan 19 - Jan 25)"]) == "2026-01-25"
    assert end_date_from_weeks(weeks_for("blue-fin"), year=config.SEASON_YEAR) \
        == "2026-09-06"


def test_end_dates_outside_the_season_window_name_the_offending_slug_and_date():
    from export_site_data import assert_end_dates_in_window

    good = [{"slug": "blue-fin", "end_date": "2026-09-06"},
            {"slug": "miriam-brooklyn", "end_date": "2026-08-16"},
            {"slug": "no-printed-date", "end_date": None}]
    assert assert_end_dates_in_window(good, "2026-08-16", "2026-09-06") is True

    stale = good + [{"slug": "left-behind", "end_date": "2026-01-25"}]
    with pytest.raises(SystemExit, match="left-behind"):
        assert_end_dates_in_window(stale, "2026-08-16", "2026-09-06")
    with pytest.raises(SystemExit, match="2026-01-25"):
        assert_end_dates_in_window(stale, "2026-08-16", "2026-09-06")
    # past the last extension week is just as wrong as before the season
    with pytest.raises(SystemExit, match="too-late"):
        assert_end_dates_in_window([{"slug": "too-late", "end_date": "2026-09-07"}],
                                   "2026-08-16", "2026-09-06")
    # a restaurant that dropped out early still sits inside the 60-day lead-in
    assert assert_end_dates_in_window(
        [{"slug": "early-out", "end_date": "2026-07-20"}],
        "2026-08-16", "2026-09-06") is True


def test_rubric_window_freezes_at_program_end_so_archive_builds_are_stable():
    from datetime import date

    import config
    from export_site_data import load_rubric, rubric_for, scoring_day

    cfg = load_rubric()
    end = date.fromisoformat(config.PROGRAM_END)
    rows = [{"slug": "blue-fin", "end_date": "2026-09-06", "subway": {}},
            {"slug": "miriam-brooklyn", "end_date": "2026-08-16", "subway": {}}]

    at_end = [rubric_for(r, cfg, scoring_day(end))["window"] for r in rows]
    after = [rubric_for(r, cfg, scoring_day(date(2027, 3, 1)))["window"] for r in rows]
    assert after == at_end
    assert scoring_day(date(2027, 3, 1)) == end
    # before the programme ends the clamp is a no-op -- the countdown is live
    mid = date(2026, 8, 20)
    assert scoring_day(mid) == mid
    assert rubric_for(rows[0], cfg, scoring_day(mid))["window"] != at_end[0]


def test_verified_asof_finds_the_date_anywhere_and_refuses_a_dateless_provenance():
    from export_site_data import VERIFIED, verified_asof

    live = json.loads(VERIFIED.read_text(encoding="utf-8"))["_doc"]["provenance"]
    assert verified_asof(live) == "2026-08-01"
    # the same fact, reworded -- the positional parse used to return "on" here
    assert verified_asof("Transcribed on 2026-08-01 from reports/…") == "2026-08-01"
    with pytest.raises(SystemExit, match="provenance"):
        verified_asof("Transcribed from the restaurants' own printed materials.")


def test_assert_tos_clean_holds_offsite_snippets_to_the_same_bar_as_menu_snippets():
    from export_site_data import SNIPPET_PAD, assert_tos_clean

    def payload(offsite):
        return {"restaurants": [{"slug": "raw-bar-co", "tags": [],
                                 "offsite_tags": offsite}]}

    assert assert_tos_clean(
        payload([{"tag": "tower", "snippet": "…chilled seafood tower for two…"}])) is True

    over = "a chilled seafood tower for two with oysters clams and shrimp" * 2
    assert len(over) > SNIPPET_PAD * 2 + 60
    with pytest.raises(SystemExit, match="raw-bar-co"):
        assert_tos_clean(payload([{"tag": "tower", "snippet": over}]))

    # two off-menu tags firing on one passage of the restaurant's own site
    with pytest.raises(SystemExit, match="overlapping"):
        assert_tos_clean(payload([
            {"tag": "tower", "snippet": "…chilled seafood tower for two…"},
            {"tag": "oysters", "snippet": "…a chilled seafood tower for two, oysters…"},
        ]))


def test_shrink_guard_refuses_a_collapsed_roster_unless_explicitly_allowed():
    from export_site_data import assert_not_shrunk

    assert assert_not_shrunk(636, 636) is True
    assert assert_not_shrunk(636, 700) is True
    assert assert_not_shrunk(636, 509) is True      # exactly the 80% floor
    assert assert_not_shrunk(0, 12) is True         # nothing published yet
    with pytest.raises(SystemExit, match="636"):
        assert_not_shrunk(636, 508)
    with pytest.raises(SystemExit, match="--allow-shrink"):
        assert_not_shrunk(636, 1)
    assert assert_not_shrunk(636, 1, allow=True) is True


def test_main_guards_the_write_without_losing_the_generated_at_only_short_circuit(
    tmp_path, monkeypatch
):
    import export_site_data as e

    def payload_of(n, stamp):
        return {"generated_at": stamp, "restaurants":
                [{"slug": f"r{i}", "tags": [], "offsite_tags": [], "rank": None,
                  "recognition": [], "lat": None, "lng": None, "subway": {},
                  "google": None} for i in range(n)],
                "book_by": e.BOOK_BY, "google_mean": 4.5}

    out = tmp_path / "restaurants.json"
    monkeypatch.setattr(e, "OUT", out)
    state = {"payload": payload_of(100, "2026-08-14T00:00:00+00:00")}
    monkeypatch.setattr(e, "build_payload",
                        lambda: (state["payload"], {"verified": 0, "estimate": 0,
                                                    "none": 0, "urgent": 0,
                                                    "outdoor_licensed": 0,
                                                    "outdoor_described_only": 0}, 0, 0))
    monkeypatch.setattr(sys, "argv", ["export_site_data.py", "--quiet"])

    e.main()
    assert json.loads(out.read_text(encoding="utf-8"))["generated_at"] \
        == "2026-08-14T00:00:00+00:00"

    # a new stamp and nothing else must NOT rewrite the file
    state["payload"] = payload_of(100, "2026-08-15T00:00:00+00:00")
    e.main()
    assert json.loads(out.read_text(encoding="utf-8"))["generated_at"] \
        == "2026-08-14T00:00:00+00:00"

    # a collapsed roster is refused, and the published file is left alone
    state["payload"] = payload_of(40, "2026-08-15T00:00:00+00:00")
    with pytest.raises(SystemExit, match="REFUSING TO SHRINK"):
        e.main()
    assert len(json.loads(out.read_text(encoding="utf-8"))["restaurants"]) == 100

    monkeypatch.setattr(sys, "argv",
                        ["export_site_data.py", "--quiet", "--allow-shrink"])
    e.main()
    assert len(json.loads(out.read_text(encoding="utf-8"))["restaurants"]) == 40
