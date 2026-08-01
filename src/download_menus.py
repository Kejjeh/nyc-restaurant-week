"""Download all Restaurant Week menu PDFs (idempotent: skips existing files)."""
import hashlib
import json
import urllib.error

from config import LISTING_DIR, MENUS_DIR, http_get


def main():
    items = json.loads((LISTING_DIR / "latest.json").read_text())["items"]
    with_menu = [it for it in items if it.get("menuFileUrl")]
    print(f"{len(with_menu)}/{len(items)} restaurants list a menu PDF", flush=True)
    manifest_path = MENUS_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    fetched = skipped = failed = 0
    for it in with_menu:
        url = it["menuFileUrl"]
        fname = url.rsplit("/", 1)[-1].split("?")[0] or f"{it['slug']}.pdf"
        dest = MENUS_DIR / fname
        if dest.exists() and dest.stat().st_size > 0:
            skipped += 1
        else:
            try:
                data = http_get(url, timeout=60)
                dest.write_bytes(data)
                fetched += 1
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
                manifest[it["slug"]] = {"url": url, "error": str(e)}
                failed += 1
                continue
        manifest[it["slug"]] = {
            "url": url,
            "file": fname,
            "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
        }
        if (fetched + skipped) % 50 == 0:
            print(f"  {fetched + skipped}/{len(with_menu)}", flush=True)
    manifest_path.write_text(json.dumps(manifest, indent=1))
    print(f"downloaded {fetched}, skipped (cached) {skipped}, failed {failed}")


if __name__ == "__main__":
    main()
