"""Parsed progress outliving the manifest means a re-downloaded menu never re-parses."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MENUS = ROOT / "data" / "raw" / "menus"
MANIFEST = json.loads((MENUS / "manifest.json").read_text(encoding="utf-8"))
PROGRESS = json.loads((MENUS / "parsed-progress.json").read_text(encoding="utf-8"))


def test_prune_drops_progress_for_slugs_the_manifest_no_longer_carries():
    from parse_menus import prune

    stale = {**PROGRESS, "gone-for-winter": {"parse_quality": "full", "courses": []}}
    kept = prune(stale, set(MANIFEST))
    assert "gone-for-winter" not in kept
    assert set(kept) == set(PROGRESS) & set(MANIFEST)
    assert kept["blue-fin"] == PROGRESS["blue-fin"]
    assert "gone-for-winter" in stale  # caller's dict untouched


def test_main_drops_departed_slugs_before_the_skip_loop(tmp_path, monkeypatch):
    import parse_menus

    cached, failed = "blue-fin", "el-coco-chelsea"
    manifest = {
        cached: MANIFEST[cached],
        failed: {"url": MANIFEST[failed]["url"], "error": "HTTP Error 503: Bad Gateway"},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    progress = {cached: PROGRESS[cached], "gone-for-winter": PROGRESS["markette"]}
    (tmp_path / "parsed-progress.json").write_text(json.dumps(progress))
    monkeypatch.setattr(parse_menus, "MENUS_DIR", tmp_path)
    parse_menus.main()

    out = json.loads((tmp_path / "parsed.json").read_text())
    assert "gone-for-winter" not in out
    assert out == json.loads((tmp_path / "parsed-progress.json").read_text())
    assert out[cached] == PROGRESS[cached]  # already parsed, so never re-opened
    assert out[failed] == {"parse_quality": "failed", "error": manifest[failed]["error"]}
