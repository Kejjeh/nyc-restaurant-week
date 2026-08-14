"""Download all Restaurant Week menu PDFs (idempotent: skips existing files)."""
import hashlib
import json
import urllib.error

from config import LISTING_DIR, MENUS_DIR, http_get


def plan_action(url, entry, dest_exists, dest_size):
    """S3 basenames repeat across seasons — a cached file is only good when the
    manifest says it came from THIS url."""
    good = entry and entry.get("url") == url and entry.get("file")
    return "skip" if good and dest_exists and dest_size > 0 else "fetch"


def merge_failure(entry, url, error):
    """Dropping the sha of a pdf that is still on disk renders downstream as
    'image-only pdf' rather than as the fetch error it is."""
    if entry and entry.get("url") == url and entry.get("file"):
        return {**entry, "error": error}
    return {"url": url, "error": error}


def prune(manifest, current_slugs):
    """Unpruned, slugs that left the program emit orphan menus rows forever."""
    return {s: e for s, e in manifest.items() if s in current_slugs}


def main():
    items = json.loads((LISTING_DIR / "latest.json").read_text())["items"]
    with_menu = [it for it in items if it.get("menuFileUrl")]
    print(f"{len(with_menu)}/{len(items)} restaurants list a menu PDF", flush=True)
    manifest_path = MENUS_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    fetched = skipped = failed = 0
    for it in with_menu:
        slug, url = it["slug"], it["menuFileUrl"]
        fname = url.rsplit("/", 1)[-1].split("?")[0] or f"{slug}.pdf"
        dest = MENUS_DIR / fname
        exists = dest.exists()
        size = dest.stat().st_size if exists else 0
        if plan_action(url, manifest.get(slug), exists, size) == "skip":
            skipped += 1
        else:
            try:
                data = http_get(url, timeout=60)
                dest.write_bytes(data)
                fetched += 1
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
                manifest[slug] = merge_failure(manifest.get(slug), url, str(e))
                failed += 1
                continue
        manifest[slug] = {
            "url": url,
            "file": fname,
            "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
        }
        if (fetched + skipped) % 50 == 0:
            print(f"  {fetched + skipped}/{len(with_menu)}", flush=True)
    # prune against the whole roster, not with_menu: a listed restaurant whose pdf
    # is not up yet must keep its cached menu
    manifest = prune(manifest, {it["slug"] for it in items})
    manifest_path.write_text(json.dumps(manifest, indent=1))
    print(f"downloaded {fetched}, skipped (cached) {skipped}, failed {failed}")


if __name__ == "__main__":
    main()
