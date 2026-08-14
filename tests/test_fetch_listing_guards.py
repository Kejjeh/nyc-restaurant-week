"""validate_listing must refuse to bless an empty/short/truncated listing."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "raw" / "listing" / "snapshot-2026-07-31.json"


def real_rows(n):
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))["items"][:n]


def test_validate_listing_rejects_empty_short_or_truncated_listings():
    from fetch_listing import validate_listing

    rows = real_rows(10)
    assert validate_listing(rows, 10, "srw26", 5) is None
    with pytest.raises(RuntimeError):
        validate_listing([], 0, "srw26", 5)
    with pytest.raises(RuntimeError, match="400"):
        validate_listing(rows, 10, "srw26", 400)
    with pytest.raises(RuntimeError, match="pagination"):
        validate_listing(rows, 100, "srw26", 5)


def test_validate_listing_flags_season_rollover_but_tolerates_missing_menus(capsys):
    from fetch_listing import validate_listing

    rows = real_rows(30)
    # a live listing whose menu PDFs all sit under a NEW S3 prefix means the
    # season rolled over and season.json is stale -- refuse loudly
    rolled = [
        {**r, "menuFileUrl": (r.get("menuFileUrl") or "").replace("srw26", "srw27")}
        for r in rows
    ]
    with pytest.raises(RuntimeError, match=r"new season\? update config/season\.json"):
        validate_listing(rolled, 30, "srw26", 5)
    # early-season: healthy row count, no menus uploaded yet -- warn, don't raise
    bare = [{**r, "menuFileUrl": ""} for r in rows]
    assert validate_listing(bare, 30, "srw26", 5) is None
    assert "menuFileUrl" in capsys.readouterr().out


def test_fetch_all_raises_on_empty_api_response_before_any_snapshot_is_written(
    tmp_path, monkeypatch
):
    import fetch_listing

    pages = tmp_path / "pages"
    pages.mkdir()
    monkeypatch.setattr(fetch_listing, "LISTING_DIR", tmp_path)
    monkeypatch.setattr(fetch_listing, "PAGES", pages)
    monkeypatch.setattr(fetch_listing, "STAMP_F", pages / "_stamp")
    monkeypatch.setattr(
        fetch_listing,
        "api_post",
        lambda body: {"total": 0, "items": [], "count": 0, "lookup": {}},
    )
    with pytest.raises(RuntimeError):
        fetch_listing.fetch_all(verbose=False)
    assert not list(tmp_path.glob("snapshot-*.json"))
    assert not (tmp_path / "latest.json").exists()
