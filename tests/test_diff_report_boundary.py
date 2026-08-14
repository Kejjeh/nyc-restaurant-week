"""At a season changeover the diff is pure noise, and the shortlist alerts lie."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LISTING = ROOT / "data" / "raw" / "listing"


def slugs(name):
    return {it["slug"] for it in json.loads((LISTING / name).read_text(encoding="utf-8"))["items"]}


def test_season_boundary_fires_on_a_roster_swap_but_not_on_a_weekly_refresh():
    from diff_report import season_boundary

    old = slugs("snapshot-2026-07-31.json")
    new = slugs("snapshot-2026-08-10.json")
    assert season_boundary(old, new) is False  # 18 dropped / 6 added in one week
    # winter keeps a few dozen returning restaurants and replaces the rest
    returning = set(sorted(old)[:60])
    winter = returning | {f"{s}-winter" for s in sorted(old)[60:]}
    assert season_boundary(old, winter) is True
    # exactly half swapped is churn, not a changeover -- both sides must clear 50%
    half = set(sorted(old)[:324]) | {f"{s}-winter" for s in sorted(old)[324:]}
    assert season_boundary(old, half) is False
    assert season_boundary(old, set()) is False  # a broken snapshot is not a season
    assert season_boundary(set(), new) is False


def summer_roster(n=100):
    """Real rows, shortlist slugs included so the alert block has something to fire on."""
    items = json.loads(
        (LISTING / "snapshot-2026-07-31.json").read_text(encoding="utf-8")
    )["items"]
    sl = json.loads(
        (ROOT / "config" / "shortlist.json").read_text(encoding="utf-8")
    )["slugs"]
    picked = [it for it in items if it["slug"] in set(sl)]
    return picked + [it for it in items if it["slug"] not in set(sl)][: n - len(picked)]


def setup_dirs(tmp_path, old_items, new_items, monkeypatch):
    import diff_report

    listing = tmp_path / "data" / "raw" / "listing"
    menus = tmp_path / "data" / "raw" / "menus"
    listing.mkdir(parents=True)
    menus.mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "shortlist.json").write_text(
        (ROOT / "config" / "shortlist.json").read_text(encoding="utf-8")
    )
    (listing / "snapshot-2026-08-10.json").write_text(json.dumps({"items": old_items}))
    (listing / "snapshot-2027-01-16.json").write_text(json.dumps({"items": new_items}))
    (menus / "manifest.json").write_text(json.dumps({"blue-fin": {"sha256": "abc"}}))
    monkeypatch.setattr(diff_report, "LISTING_DIR", listing)
    monkeypatch.setattr(diff_report, "MENUS_DIR", menus)
    return diff_report, menus


def test_main_suppresses_the_whole_report_at_a_season_changeover(
    tmp_path, monkeypatch, capsys
):
    old_items = summer_roster()
    winter = [
        it if i < 10 else {**it, "slug": f"{it['slug']}-winter"}
        for i, it in enumerate(old_items)
    ]
    diff_report, menus = setup_dirs(tmp_path, old_items, winter, monkeypatch)

    assert diff_report.main() == 0
    out = capsys.readouterr().out
    assert "season boundary" in out
    assert "roster replaced: 90 dropped, 90 added" in out
    assert "DROPPED from program" not in out
    assert "SHORTLIST ALERTS" not in out
    assert "added (" not in out and "dropped (" not in out
    assert not (menus / "manifest_history.json").exists()


def test_a_normal_week_still_alerts_on_a_shortlist_drop_and_writes_history(
    tmp_path, monkeypatch, capsys
):
    old_items = summer_roster()
    sl = set(json.loads(
        (ROOT / "config" / "shortlist.json").read_text(encoding="utf-8")
    )["slugs"])
    gone = next(it["slug"] for it in old_items if it["slug"] in sl)
    new_items = [it for it in old_items if it["slug"] != gone]
    diff_report, menus = setup_dirs(tmp_path, old_items, new_items, monkeypatch)

    assert diff_report.main() == 0
    out = capsys.readouterr().out
    assert "season boundary" not in out
    assert f"DROPPED from program: {gone}" in out
    assert "dropped (1):" in out
    hist = json.loads((menus / "manifest_history.json").read_text())
    assert hist == {"blue-fin": {"sha256": "abc"}}
