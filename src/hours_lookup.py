"""On-demand opening-hours lookup for a single restaurant (cached).

Deliberately NOT run in bulk: hours change and 648 lookups would be wasteful.
Usage:
    python src/hours_lookup.py manhatta
    python src/hours_lookup.py "Sushi by Bou"          # name also works
Strategy: fetch the restaurant's own website and look for hours markup
(JSON-LD OpeningHoursSpecification, then loose text heuristics). Result is
cached in data/cache/hours.json; pass --refresh to re-fetch.
"""
import json
import re
import sys

from config import CACHE_DIR, LISTING_DIR, http_get

CACHE = CACHE_DIR / "hours.json"


def find_restaurant(key):
    items = json.loads((LISTING_DIR / "latest.json").read_text())["items"]
    key_l = key.lower()
    for it in items:
        if it["slug"] == key_l:
            return it
    for it in items:
        if key_l in it["shortTitle"].lower():
            return it
    raise SystemExit(f"No restaurant matching {key!r} in latest snapshot.")


def hours_from_jsonld(html):
    out = []
    for m in re.finditer(
        r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            for g in [node] + node.get("@graph", []) if isinstance(node, dict) else []:
                spec = g.get("openingHoursSpecification") or g.get("openingHours")
                if spec:
                    out.append(spec)
    return out


def hours_from_text(html):
    text = re.sub(r"<[^>]+>", " ", html)
    pat = re.compile(
        r"((?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s*(?:-|–|to|through)?\s*"
        r"(?:mon|tue|wed|thu|fri|sat|sun)?[a-z]*\.?\s*:?\s*"
        r"\d{1,2}(?::\d{2})?\s*(?:am|pm)\s*(?:-|–|to)\s*"
        r"\d{1,2}(?::\d{2})?\s*(?:am|pm))",
        re.I,
    )
    return list(dict.fromkeys(m.group(1).strip() for m in pat.finditer(text)))[:14]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    it = find_restaurant(" ".join(args))
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if it["slug"] in cache and "--refresh" not in sys.argv:
        print(json.dumps(cache[it["slug"]], indent=1))
        return
    site = it.get("website")
    result = {"name": it["shortTitle"], "website": site, "hours": None, "source": None}
    if site:
        try:
            html = http_get(site, timeout=30).decode("utf-8", "replace")
            jl = hours_from_jsonld(html)
            if jl:
                result["hours"], result["source"] = jl, "jsonld"
            else:
                tx = hours_from_text(html)
                if tx:
                    result["hours"], result["source"] = tx, "text-heuristic"
        except Exception as e:
            result["error"] = str(e)
    if not result["hours"]:
        result["note"] = (
            "No hours found on website; check Google Maps: "
            f"https://www.google.com/maps/search/{it['shortTitle'].replace(' ', '+')}+NYC"
        )
    cache[it["slug"]] = result
    CACHE.write_text(json.dumps(cache, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
