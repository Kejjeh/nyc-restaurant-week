"""The archive has to outlive the season that wrote it.

Summer 2026 must stay readable once Winter 2027 is the live roster, so the
registry is a MERGE and never a rewrite: an exporter run speaks only for its own
season code and leaves every other entry exactly as it found it.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUMMER = json.loads((ROOT / "config" / "season.json").read_text(encoding="utf-8"))
WINTER = {"code": "srw27", "label": "Winter 2027", "year": 2027,
          "start": "2027-01-18", "book_by": "2027-01-24", "end": "2027-02-07",
          "min_rows": 400}


def test_update_registry_creates_the_registry_from_nothing():
    from export_site_data import update_registry

    [e] = update_registry(None, SUMMER, date(2026, 8, 14))
    assert e == {"code": "srw26", "label": "Summer 2026", "year": 2026,
                 "start": "2026-07-20", "book_by": "2026-08-16",
                 "end": "2026-09-06", "status": "live",
                 "file": "seasons/srw26.json"}
    # min_rows and _doc are build-time facts, not published ones
    assert "min_rows" not in e and "_doc" not in e
    assert update_registry([], SUMMER, "2026-08-14") == [e]


def test_update_registry_upserts_its_own_code_and_leaves_a_foreign_season_alone():
    from export_site_data import update_registry

    winter = update_registry(None, WINTER, date(2027, 1, 20))
    both = update_registry(winter, SUMMER, date(2027, 1, 20))
    assert [e["code"] for e in both] == ["srw27", "srw26"]   # newest end first
    assert both[0] == winter[0]                              # untouched, incl. status
    assert both[1]["status"] == "archived"

    # a second run of the same season rewrites only its own row
    relabelled = {**SUMMER, "label": "Summer 2026 (archive)"}
    again = update_registry(both, relabelled, date(2027, 1, 20))
    assert len(again) == 2 and again[0] == winter[0]
    assert again[1]["label"] == "Summer 2026 (archive)"

    # a hand-added entry for a season this pipeline never built survives too
    hand = [{"code": "srw25", "label": "Summer 2025", "year": 2025,
             "start": "2025-07-21", "book_by": "2025-08-17", "end": "2025-08-17",
             "status": "archived", "file": "seasons/srw25.json"}]
    merged = update_registry(hand + both, WINTER, date(2027, 1, 20))
    assert [e["code"] for e in merged] == ["srw27", "srw26", "srw25"]
    assert merged[2] == hand[0]


def test_update_registry_archives_a_season_the_day_after_it_ends():
    from export_site_data import update_registry

    def status(today):
        return update_registry(None, SUMMER, today)[0]["status"]

    assert status(date(2026, 8, 14)) == "live"
    assert status(date(2026, 9, 6)) == "live"      # the last extension day counts
    assert status(date(2026, 9, 7)) == "archived"
    assert status("2026-09-07") == "archived"      # ISO string, same answer


def payload_of(n, stamp="2026-08-14T00:00:00+00:00"):
    return {"generated_at": stamp,
            "restaurants": [{"slug": f"r{i}", "tags": [], "offsite_tags": [],
                             "rank": None, "recognition": [], "lat": None,
                             "lng": None, "subway": {}, "google": None}
                            for i in range(n)],
            "book_by": "2026-08-16", "google_mean": 4.5}


def exporter(monkeypatch, tmp_path, payload, *argv):
    """main() pointed at a throwaway docs/data, since it writes three files."""
    import export_site_data as e

    monkeypatch.setattr(e, "OUT", tmp_path / "restaurants.json")
    monkeypatch.setattr(e, "build_payload",
                        lambda: (payload["p"], {"verified": 0, "estimate": 0,
                                                "none": 0, "urgent": 0,
                                                "outdoor_licensed": 0,
                                                "outdoor_described_only": 0}, 0, 0))
    monkeypatch.setattr(sys, "argv", ["export_site_data.py", *argv])
    return e


def test_main_writes_the_season_payload_the_registry_and_the_legacy_copy(
    tmp_path, monkeypatch
):
    state = {"p": payload_of(100)}
    e = exporter(monkeypatch, tmp_path, state, "--quiet")
    e.main()

    season = tmp_path / "seasons" / "srw26.json"
    assert len(json.loads(season.read_text(encoding="utf-8"))["restaurants"]) == 100
    # the legacy file is still written verbatim: the published frontend reads it
    assert json.loads((tmp_path / "restaurants.json").read_text(encoding="utf-8")) \
        == json.loads(season.read_text(encoding="utf-8"))

    reg = json.loads((tmp_path / "seasons.json").read_text(encoding="utf-8"))["seasons"]
    assert [s["code"] for s in reg] == ["srw26"]
    assert reg[0]["file"] == "seasons/srw26.json"

    # a season this build knows nothing about is still there afterwards
    (tmp_path / "seasons.json").write_text(json.dumps({"seasons": reg + [
        {"code": "srw25", "label": "Summer 2025", "year": 2025,
         "start": "2025-07-21", "book_by": "2025-08-17", "end": "2025-08-17",
         "status": "archived", "file": "seasons/srw25.json"}]}), encoding="utf-8")
    state["p"] = payload_of(101)
    e.main()
    reg = json.loads((tmp_path / "seasons.json").read_text(encoding="utf-8"))["seasons"]
    assert [s["code"] for s in reg] == ["srw26", "srw25"]


def test_check_names_both_targets_and_writes_nothing(tmp_path, monkeypatch, capsys):
    e = exporter(monkeypatch, tmp_path, {"p": payload_of(100)}, "--check")
    e.main()

    assert not (tmp_path / "seasons").exists()
    assert not (tmp_path / "seasons.json").exists()
    assert not (tmp_path / "restaurants.json").exists()
    printed = capsys.readouterr().out
    assert "seasons/srw26.json" in printed.replace("\\", "/")
    assert "seasons.json" in printed


def test_shrink_guard_measures_against_the_season_file_not_the_legacy_copy(
    tmp_path, monkeypatch
):
    state = {"p": payload_of(100)}
    e = exporter(monkeypatch, tmp_path, state, "--quiet")
    e.main()
    # the legacy copy goes stale the moment the frontend stops being served it
    (tmp_path / "restaurants.json").write_text(
        json.dumps(payload_of(4)), encoding="utf-8")

    state["p"] = payload_of(40)
    with pytest.raises(SystemExit, match="100 -> 40"):
        e.main()
    season = tmp_path / "seasons" / "srw26.json"
    assert len(json.loads(season.read_text(encoding="utf-8"))["restaurants"]) == 100
