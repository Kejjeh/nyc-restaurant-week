"""Fetch each restaurant's detail page to extract street address (+ lat/long).

Addresses are not in the listing API; they live in the RSC payload of
https://www.nyctourism.com/restaurant-week/{slug}/ . Cached per slug in
data/raw/details/{slug}.json — re-runs only fetch new slugs (use --refresh
to force).
"""
import json
import re
import sys
import urllib.error

from config import DETAILS_DIR, LISTING_DIR, SITE, http_get


def parse_detail(html):
    out = {}
    m = re.search(r'\\"address\\":\\"(.*?)\\"', html)
    if m:
        out["address"] = m.group(1).encode().decode("unicode_escape")
    m = re.search(r'directionHref.*?Current\+Location/(-?[\d.]+),(-?[\d.]+)', html)
    if m:
        out["lat"], out["lng"] = float(m.group(1)), float(m.group(2))
    m = re.search(r'\\"phone\\":\\"([^\\"]+)\\"', html)
    if m:
        out["phone"] = m.group(1)
    return out


def main(force=False):
    items = json.loads((LISTING_DIR / "latest.json").read_text())["items"]
    todo = [
        it["slug"]
        for it in items
        if force or not (DETAILS_DIR / f"{it['slug']}.json").exists()
    ]
    print(f"{len(todo)} detail pages to fetch (of {len(items)})", flush=True)
    for i, slug in enumerate(todo, 1):
        try:
            html = http_get(f"{SITE}/restaurant-week/{slug}/").decode("utf-8", "replace")
            d = parse_detail(html)
            d["slug"] = slug
            d["listing_url"] = f"{SITE}/restaurant-week/{slug}/"
        except urllib.error.HTTPError as e:
            d = {"slug": slug, "error": str(e)}
        (DETAILS_DIR / f"{slug}.json").write_text(json.dumps(d))
        if i % 50 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
    ok = sum(1 for f in DETAILS_DIR.glob("*.json") if "address" in f.read_text())
    print(f"details cached: {len(list(DETAILS_DIR.glob('*.json')))}, with address: {ok}")


if __name__ == "__main__":
    main(force="--refresh" in sys.argv)
