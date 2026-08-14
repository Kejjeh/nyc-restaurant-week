"""The menu cache must survive a season rollover, a fetch failure, and departures."""
import hashlib
import json
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MENUS = ROOT / "data" / "raw" / "menus"
MANIFEST = json.loads((MENUS / "manifest.json").read_text(encoding="utf-8"))


def test_plan_action_refetches_when_the_url_moved_season_despite_a_cached_basename():
    from download_menus import plan_action

    entry = MANIFEST["la-contenta-oeste"]
    assert plan_action(entry["url"], entry, True, 4096) == "skip"
    # winter reuses the srw26 basename under srw27 -- the cached pdf is last season's
    winter = entry["url"].replace("srw26", "srw27")
    assert plan_action(winter, entry, True, 4096) == "fetch"
    assert plan_action(entry["url"], entry, False, 0) == "fetch"  # file vanished
    assert plan_action(entry["url"], entry, True, 0) == "fetch"  # truncated download
    assert plan_action(entry["url"], None, True, 4096) == "fetch"  # never manifested
    prior_error = {"url": entry["url"], "error": "HTTP Error 503: Service Unavailable"}
    assert plan_action(entry["url"], prior_error, True, 4096) == "fetch"


def test_merge_failure_keeps_the_last_good_pdf_instead_of_erasing_its_sha():
    from download_menus import merge_failure

    entry = MANIFEST["gyu-kaku-midtown"]
    err = "HTTP Error 504: Gateway Timeout"
    kept = merge_failure(entry, entry["url"], err)
    assert kept["file"] == entry["file"] and kept["sha256"] == entry["sha256"]
    assert kept["error"] == err
    assert "error" not in entry  # the manifest entry itself is untouched
    assert merge_failure(None, entry["url"], err) == {"url": entry["url"], "error": err}
    # the pdf on disk is last season's, so a failed refetch must not vouch for it
    winter = entry["url"].replace("srw26", "srw27")
    assert merge_failure(entry, winter, err) == {"url": winter, "error": err}


def test_prune_drops_manifest_entries_for_slugs_that_left_the_program():
    from download_menus import prune

    departed = {"la-contenta-oeste", "markette"}
    current = set(MANIFEST) - departed
    kept = prune(MANIFEST, current)
    assert set(kept) == current
    assert kept["gyu-kaku-midtown"] == MANIFEST["gyu-kaku-midtown"]
    assert len(MANIFEST) == len(current) + 2  # the manifest itself is untouched
    assert prune(MANIFEST, set()) == {}


def test_main_rolls_the_cache_over_a_season_without_losing_a_failed_slugs_sha(
    tmp_path, monkeypatch
):
    import download_menus

    listing, menus = tmp_path / "listing", tmp_path / "menus"
    listing.mkdir()
    menus.mkdir()
    items = json.loads(
        (ROOT / "data" / "raw" / "listing" / "latest.json").read_text(encoding="utf-8")
    )["items"]
    keep, moved, fails = "blue-fin", "el-coco-chelsea", "miriam-brooklyn"
    bare = "ammos-estiatorio-grand-central"  # still listed, menu pdf not up yet
    rows = {it["slug"]: it for it in items if it["slug"] in (keep, moved, fails, bare)}
    rows[moved] = {
        **rows[moved],
        "menuFileUrl": rows[moved]["menuFileUrl"].replace("srw26", "srw27"),
    }
    rows[bare] = {**rows[bare], "menuFileUrl": ""}
    (listing / "latest.json").write_text(json.dumps({"items": list(rows.values())}))
    manifest = {s: MANIFEST[s] for s in (keep, moved, fails, bare)}
    manifest["gone-for-winter"] = MANIFEST["markette"]
    for s in (keep, moved):
        body = b"%PDF-1.4 summer " + s.encode()
        (menus / manifest[s]["file"]).write_bytes(body)
        manifest[s] = {**manifest[s], "sha256": hashlib.sha256(body).hexdigest()}
    (menus / manifest[fails]["file"]).write_bytes(b"")  # truncated by an earlier run
    (menus / "manifest.json").write_text(json.dumps(manifest))

    winter_pdf = b"%PDF-1.4 winter menu"
    asked = []

    def fake_get(url, timeout=60):
        asked.append(url)
        if fails in rows and url == rows[fails]["menuFileUrl"]:
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)
        return winter_pdf

    monkeypatch.setattr(download_menus, "LISTING_DIR", listing)
    monkeypatch.setattr(download_menus, "MENUS_DIR", menus)
    monkeypatch.setattr(download_menus, "http_get", fake_get)
    download_menus.main()

    out = json.loads((menus / "manifest.json").read_text())
    assert asked == [rows[moved]["menuFileUrl"], rows[fails]["menuFileUrl"]]
    assert out[keep] == manifest[keep]  # cached, untouched
    assert out[moved]["url"] == rows[moved]["menuFileUrl"]
    assert out[moved]["sha256"] == hashlib.sha256(winter_pdf).hexdigest()
    assert (menus / out[moved]["file"]).read_bytes() == winter_pdf
    assert out[fails]["sha256"] == manifest[fails]["sha256"]  # last good pdf survives
    assert "503" in out[fails]["error"]
    assert out[bare] == manifest[bare]  # in the program, so not pruned
    assert "gone-for-winter" not in out
